"""Netplay Matchmaker server (protocol v1; see PROTOCOL.md).

TCP port N   : matchmaking. One connection per search, newline-delimited JSON. Closing cancels.
HTTP port N+1: a tiny web page for invite links (opens the app, or says how to get it).

A client waits in every lane it offered. The first compatible opponent in ANY lane wins, and both
are removed from every pool at once. Compatible = same game, same lane, and at least one can host.
Invites: a search may carry an invite code. It then also waits in a PRIVATE room (game + code).
`invite_only` clients wait ONLY in that private room, so a friend with a link meets the person who
shared it, while that person stays open to randoms in the public pool unless they chose otherwise.
"""
import asyncio
import html
import json
import os
import re
import sys
from urllib.parse import parse_qs, quote, urlsplit

import sessions

PING_SECONDS = 20          # keeps phones on flaky mobile networks from lingering as ghosts
LANES = ("native", "stream")
CODE_RE = re.compile(r"^[A-Za-z0-9]{4,32}$")
GAME_RE = re.compile(r"^[a-z0-9]+:[a-z0-9-]+$")
HOST_RE = re.compile(r"^[A-Za-z0-9.\-\[\]:]{1,255}$")
pools = {}                 # (room key, lane) -> [Waiter]


class Waiter:
    def __init__(self, writer, req):
        self.writer = writer
        self.game = req["game"]
        self.lanes = [l for l in req["lanes"] if l in LANES]
        self.info = req.get("info") if isinstance(req.get("info"), dict) else {}
        ch = req.get("can_host") if isinstance(req.get("can_host"), dict) else {}
        self.can_host = {l: bool(ch.get(l, True)) for l in LANES}   # old clients: assume yes
        self.client = str(req.get("client", "unknown"))[:40]
        self.ip = (writer.get_extra_info("peername") or [""])[0]
        invite = req.get("invite") or ""
        only = bool(req.get("invite_only"))
        # rooms to wait in, most specific first
        self.keys = ([f"{self.game}#{invite}"] if invite else []) + ([] if only else [self.game])
        self.done = False


def validate(req):
    if not isinstance(req, dict) or req.get("op") != "search":
        return "first message must be {\"op\":\"search\", ...}"
    if not isinstance(req.get("game"), str) or not req["game"] or len(req["game"]) > 200 or "#" in req["game"]:
        return "game must be a non-empty string"
    lanes = req.get("lanes")
    if not isinstance(lanes, list) or not any(l in LANES for l in lanes):
        return "lanes must list at least one of native, stream"
    inv = req.get("invite")
    if inv is not None and (not isinstance(inv, str) or not CODE_RE.match(inv)):
        return "invite must be 4 to 32 letters or digits"
    if req.get("invite_only") and not inv:
        return "invite_only needs an invite code"
    return ""


def leave_all(w):
    for key in w.keys:
        for lane in w.lanes:
            lst = pools.get((key, lane), [])
            if w in lst:
                lst.remove(w)


def send(w, msg):
    w.writer.write((json.dumps(msg) + "\n").encode())


def stream_backend(w):
    st = w.info.get("stream")
    if not isinstance(st, dict):
        return None
    return st.get("backend") or ("parsec" if st.get("peer_id") else None)


def compatible(a, b, lane):
    """Stream players must use the same streaming method. Old clients that say nothing match anything."""
    if lane != "stream":
        return True
    x, y = stream_backend(a), stream_backend(b)
    return x is None or y is None or x == y


def wants_own_stream(w):
    st = w.info.get("stream")
    return isinstance(st, dict) and st.get("backend") == "own"


def peer_view(other, lane):
    return {**(other.info.get(lane) if isinstance(other.info.get(lane), dict) else {}),
            "ip": other.ip, "client": other.client}


async def pinger(w):
    while not w.done:
        await asyncio.sleep(PING_SECONDS)
        try:
            send(w, {"op": "ping"})
            await w.writer.drain()
        except Exception:
            w.writer.close()
            return


async def handle(reader, writer):
    w = ping = None
    try:
        line = await reader.readline()
        if not line:
            return
        try:
            req = json.loads(line)
        except ValueError:
            req = None
        problem = validate(req)
        if problem:
            writer.write((json.dumps({"op": "error", "msg": problem}) + "\n").encode())
            await writer.drain()
            return
        w = Waiter(writer, req)
        for key in w.keys:
            for lane in w.lanes:
                for other in list(pools.get((key, lane), [])):
                    if other.done or not compatible(other, w, lane):
                        continue
                    if other.can_host[lane]:
                        host, guest = other, w
                    elif w.can_host[lane]:
                        host, guest = w, other
                    else:
                        continue
                    leave_all(other)
                    other.done = w.done = True
                    via = "invite" if "#" in key else "public"
                    to_host = {"op": "matched", "v": 1, "lane": lane, "role": "host", "via": via, "peer": peer_view(guest, lane)}
                    to_guest = {"op": "matched", "v": 1, "lane": lane, "role": "guest", "via": via, "peer": peer_view(host, lane)}
                    if lane == "stream" and wants_own_stream(host) and wants_own_stream(guest):
                        sess = sessions.create(w.game)
                        if sess:    # else: server is full, fall through to the plain match
                            to_host["session"] = {"key": sess.keys["host"]}
                            to_guest["session"] = {"key": sess.keys["guest"], "play": f"/play?t={sess.keys['guest']}"}
                    send(host, to_host)
                    send(guest, to_guest)
                    print(f"matched on {lane} ({w.game}, {via}) host={host.client} guest={guest.client}", flush=True)
                    await writer.drain()
                    return
        for key in w.keys:
            for lane in w.lanes:
                pools.setdefault((key, lane), []).append(w)
        print(f"waiting: {w.game} lanes={w.lanes} client={w.client}" + (" (invite)" if any("#" in k for k in w.keys) else ""), flush=True)
        ping = asyncio.create_task(pinger(w))
        await reader.read()  # until the client cancels (closes) or the connection dies
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        if ping:
            ping.cancel()
        if w:
            w.done = True
            leave_all(w)
        writer.close()


# ----------------------------------------------------------------- invite landing page
def landing_page(host_header, tcp_port, game, code):
    """HTML for an invite link. Everything that came from the URL is validated and escaped."""
    host = (host_header or "").rsplit(":", 1)[0] if host_header and not host_header.endswith("]") else (host_header or "")
    ok = bool(HOST_RE.match(host)) and bool(GAME_RE.match(game or "")) and bool(CODE_RE.match(code or ""))
    if not ok:
        return ("<h1>Netplay Matchmaker</h1><p>This invite link looks incomplete or damaged. "
                "Ask your friend to send it again.</p>")
    title = html.escape(game.split(":", 1)[1].replace("-", " ").title())
    deep = "netplay://join?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in
                                        (("host", host), ("port", tcp_port), ("g", game), ("c", code)))
    return (f"<h1>You're invited to play {title}</h1>"
            f"<p><a class='btn' href='{html.escape(deep, quote=True)}'>Open in Netplay Matchmaker</a></p>"
            "<p>Don't have the app yet? Install Netplay Matchmaker on your phone or computer, "
            "then come back to this page and tap the button again.</p>"
            "<p>The button does nothing? In the app choose <b>Join with a link</b> and paste this "
            "page's address.</p>")


DEMO = bool(os.environ.get("NETPLAY_DEMO"))   # --demo: lets /demo make test streams with no second player
DEMO_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; "
            "frame-src 'self'; media-src 'self' blob:; img-src 'self' data:")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def read_web(name):
    with open(os.path.join(WEB_DIR, name), "rb") as f:
        return f.read()


def page(body):
    return ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Netplay Matchmaker</title><style>body{font-family:sans-serif;max-width:32rem;margin:2rem auto;padding:0 1rem;line-height:1.5}"
            ".btn{display:inline-block;padding:.9rem 1.4rem;background:#2d7ff9;color:#fff;border-radius:.5rem;text-decoration:none;font-weight:bold}"
            f"</style></head><body>{body}</body></html>")


PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'"
PLAY_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "connect-src 'self'; media-src 'self' blob:; img-src 'self' data:")


def respond(status, body, ctype="text/html; charset=utf-8", csp=PAGE_CSP):
    data = body if isinstance(body, bytes) else body.encode()
    return (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(data)}\r\n"
            f"Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n"
            f"Content-Security-Policy: {csp}\r\nConnection: close\r\n\r\n").encode() + data


def json_response(status, obj):
    return respond(status, json.dumps(obj), "application/json", "default-src 'none'")


async def stream_routes(method, u, q, body):
    """/play (guest page), /cfg, /sig (signaling). Returns raw HTTP bytes, or None if not ours."""
    first = lambda k: (q.get(k) or [""])[0][:200]
    if DEMO and method == "GET" and u.path == "/demo":
        return respond("200 OK", read_web("demo.html"), csp=DEMO_CSP)
    if DEMO and method == "GET" and u.path == "/demo/new":
        sess = sessions.create("demo:test")
        if not sess:
            return json_response("503 Service Unavailable", {"error": "server is full"})
        return json_response("200 OK", {"host": sess.keys["host"], "guest": sess.keys["guest"]})
    if u.path == "/play" and method == "GET":
        hit = sessions.lookup(first("t"))
        if not hit or hit[1] != "guest":
            return respond("404 Not Found", page("<h1>This stream link has ended</h1>"
                           "<p>Ask your friend to start a new session and send you the new link.</p>"))
        return respond("200 OK", read_web("guest.html"), csp=PLAY_CSP)
    if u.path in ("/cfg", "/sig"):
        hit = sessions.lookup(first("t"))
        if not hit:
            return json_response("404 Not Found", {"error": "session ended"})
        sess, role = hit
        if u.path == "/cfg" and method == "GET":
            return json_response("200 OK", {"iceServers": sessions.ice_servers(), "role": role,
                                            "game": sess.game, "v": 1})
        if u.path == "/sig" and method == "GET":
            wait = first("wait")
            msgs = await sessions.poll(sess, role, float(wait) if wait.replace(".", "", 1).isdigit() else 0)
            return json_response("200 OK", {"msgs": msgs, "closed": sess.closed})
        if u.path == "/sig" and method == "POST":
            try:
                msg = json.loads(body.decode("utf-8"))
            except ValueError:
                return json_response("400 Bad Request", {"error": "body must be JSON"})
            err = sessions.push(sess, role, msg)
            return json_response("400 Bad Request", {"error": err}) if err else json_response("200 OK", {"ok": True})
        return json_response("405 Method Not Allowed", {"error": "not allowed"})
    return None


async def http_handle(reader, writer, tcp_port=7777):
    try:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return
    try:
        head = raw.decode("latin-1").split("\r\n")
        method, target, _ = head[0].split(" ", 2)
        headers = {h.split(":", 1)[0].strip().lower(): h.split(":", 1)[1].strip() for h in head[1:] if ":" in h}
        u = urlsplit(target)
        q = parse_qs(u.query)
        body, out = b"", None
        if method == "POST":
            n = headers.get("content-length", "")
            if not n.isdigit() or int(n) > sessions.MAX_BODY:
                out = json_response("413 Payload Too Large", {"error": "body too large"})
            elif int(n):
                body = await asyncio.wait_for(reader.readexactly(int(n)), 5)
        if out is None:
            out = await stream_routes(method, u, q, body)
        if out is None:
            first = lambda k: (q.get(k) or [""])[0][:200]
            if method != "GET":
                out = respond("405 Method Not Allowed", page("<p>Not allowed.</p>"))
            elif u.path == "/join":
                port = first("p")
                out = respond("200 OK", page(landing_page(headers.get("host", ""),
                              int(port) if port.isdigit() and 0 < int(port) < 65536 else tcp_port,
                              first("g"), first("c"))))
            else:
                out = respond("200 OK", page("<h1>Netplay Matchmaker</h1><p>The matchmaker is running.</p>"))
    except (ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        out = respond("400 Bad Request", page("<p>Bad request.</p>"))
    try:
        writer.write(out)
        await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


async def main(port, http_port=None):
    http_port = port + 1 if http_port is None else http_port
    srv = await asyncio.start_server(handle, "0.0.0.0", port)
    print(f"matchmaker on :{port}", flush=True)
    if http_port:
        try:
            await asyncio.start_server(lambda r, w: http_handle(r, w, port), "0.0.0.0", http_port)
            print(f"invite page on :{http_port}", flush=True)
        except OSError as e:
            print(f"invite page disabled ({e})", flush=True)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if x != "--demo"]
    if "--demo" in sys.argv:
        DEMO = True
        print("demo mode: open /demo on the web port to test streaming with no second player", flush=True)
    asyncio.run(main(int(args[0]) if args else 7777, int(args[1]) if len(args) > 1 else None))
