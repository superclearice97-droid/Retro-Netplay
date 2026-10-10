"""Netplay Matchmaker server (protocol v1; see PROTOCOL.md).

TCP port N   : matchmaking. One connection per search, newline-delimited JSON. Closing cancels.
HTTP port N+1: a tiny web page for invite links (opens the app, or says how to get it).

A client waits in every lane it offered. The first compatible opponent in ANY lane wins, and both
are removed from every pool at once. Compatible = same game, same lane, and at least one can host.
Invites: a search may carry an invite code. It then also waits in a PRIVATE room (game + code).
`invite_only` clients wait ONLY in that private room, so a friend with a link meets the person who
shared it, while that person stays open to randoms in the public pool unless they chose otherwise.

Friends (optional): a player may send a random secret (`me`) and the ids of their friends. Their id is a hash
of the secret, so nobody can pretend to be them. Two players are friends only if BOTH list each other. In
the public pool friends are matched first (players marked priority before other friends); a player with
friends waits `friend_wait` seconds before strangers are allowed. `friends_only` never allows strangers.
A separate `hello` connection keeps you visible to your friends while you're online, and carries calls
("play with me" / "let me watch") between friends. The server keeps none of this after you disconnect.
"""
import asyncio
import hashlib
import html
import json
import os
import re
import secrets
import ssl
import time
import sys
from urllib.parse import parse_qs, quote, urlsplit

import sessions

PING_SECONDS = 20          # keeps phones on flaky mobile networks from lingering as ghosts
LANES = ("native", "stream")
CODE_RE = re.compile(r"^[A-Za-z0-9]{4,32}$")
GAME_RE = re.compile(r"^[a-z0-9]+:[a-z0-9-]+$")
HOST_RE = re.compile(r"^[A-Za-z0-9.\-\[\]:]{1,255}$")
pools = {}                 # (room key, lane) -> [Waiter]

# Abuse limits. A search is one small JSON line; nothing legitimate comes close to these.
FIRST_LINE_TIMEOUT = 10    # seconds to send the search line before we hang up
MAX_LINE = 8192            # bytes in that line
MAX_PER_IP = 8             # searches one address may have waiting at once
MAX_WAITING = 5000         # searches waiting on the whole server
SEARCHES_PER_MIN = 30      # new searches one address may start per minute (stops invite-code guessing)
INFO_KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
open_by_ip = {}            # ip -> searches currently waiting
recent_by_ip = {}          # ip -> [start times in the last minute]

# Friends
ID_RE = re.compile(r"^[0-9a-f]{24}$")
SECRET_RE = re.compile(r"^[0-9a-f]{32,64}$")
CALL_RE = re.compile(r"^[A-Za-z0-9]{6,32}$")
MAX_FRIENDS = 200
FRIEND_WAIT = 15           # default seconds a player with friends waits for one before strangers count
FRIEND_WAIT_MAX = 60
MAX_PRESENCE_PER_IP = 8
MAX_PRESENCE = 20000
CALLS_PER_MIN = 10
CALL_SECONDS = 60          # an unanswered call expires
STATES = ("idle", "queue", "playing")
online = {}                # player id -> Presence (only while connected)
presence_by_ip = {}
pending_calls = {}         # (caller id, callee id, call id) -> (game, kind, expiry)


def player_id(secret):
    """Public id = start of SHA-256 of the secret. Friends share ids; only the owner knows the secret."""
    return hashlib.sha256(secret.encode("ascii")).hexdigest()[:24]


def clean_name(n):
    if not isinstance(n, str):
        return ""
    n = "".join(c for c in n if c.isprintable() and c not in "<>\"\\")
    return " ".join(n.split())[:24]


def id_set(v):
    if not isinstance(v, list):
        return set()
    return {x for x in v[:MAX_FRIENDS] if isinstance(x, str) and ID_RE.match(x)}


def clean_info(info):
    """Keep only what an opponent needs: {lane: {short_key: short string/number/bool}}. Nothing nested,
    nothing long, so one player can't push arbitrary data (or huge blobs) to another through us."""
    out = {}
    if not isinstance(info, dict):
        return out
    for lane in LANES:
        d = info.get(lane)
        if not isinstance(d, dict):
            continue
        clean = {}
        for k, v in list(d.items())[:8]:
            if k in ("ip", "client") or not isinstance(k, str) or not INFO_KEY_RE.match(k):
                continue
            if isinstance(v, bool) or (isinstance(v, (int, float)) and abs(v) < 1e12):
                clean[k] = v
            elif isinstance(v, str) and len(v) <= 64 and v.isprintable():
                clean[k] = v
        out[lane] = clean
    return out


def admit(ip, now):
    """'' if this address may start another search, else a reason."""
    times = [t for t in recent_by_ip.get(ip, []) if now - t < 60]
    recent_by_ip[ip] = times
    if len(recent_by_ip) > 20000:   # forget idle addresses so this table can't grow forever
        for k in [k for k, v in recent_by_ip.items() if not v or now - v[-1] >= 60]:
            recent_by_ip.pop(k, None)
    if len(times) >= SEARCHES_PER_MIN:
        return "too many searches from your address; wait a minute and try again"
    if open_by_ip.get(ip, 0) >= MAX_PER_IP:
        return "too many searches waiting from your address"
    if sum(open_by_ip.values()) >= MAX_WAITING:
        return "the matchmaker is busy; try again in a moment"
    times.append(now)
    recent_by_ip[ip] = times
    return ""


class Waiter:
    def __init__(self, writer, req):
        self.writer = writer
        self.game = req["game"]
        self.lanes = [l for l in req["lanes"] if l in LANES]
        self.info = clean_info(req.get("info"))
        ch = req.get("can_host") if isinstance(req.get("can_host"), dict) else {}
        self.can_host = {l: bool(ch.get(l, True)) for l in LANES}   # old clients: assume yes
        self.client = re.sub(r"[^A-Za-z0-9 ._-]", "", str(req.get("client", "unknown")))[:40] or "unknown"
        self.ip = (writer.get_extra_info("peername") or [""])[0]
        invite = req.get("invite") or ""
        only = bool(req.get("invite_only"))
        # rooms to wait in, most specific first
        self.keys = ([f"{self.game}#{invite}"] if invite else []) + ([] if only else [self.game])
        self.done = False
        me = req.get("me")
        self.id = player_id(me) if isinstance(me, str) and SECRET_RE.match(me) else ""
        self.name = clean_name(req.get("name"))
        self.friends = id_set(req.get("friends")) if self.id else set()
        self.priority = id_set(req.get("priority")) & self.friends
        self.friends_only = bool(req.get("friends_only")) and bool(self.id)
        self.prefer_host = bool(req.get("prefer_host"))
        fw = req.get("friend_wait")
        wait = fw if isinstance(fw, (int, float)) and not isinstance(fw, bool) else FRIEND_WAIT
        wait = max(0.0, min(float(wait), FRIEND_WAIT_MAX)) if self.friends else 0.0
        self.since = time.monotonic()
        self.open_at = self.since + wait
        self.timer = None


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
    return validate_social(req)


def validate_social(req):
    me = req.get("me")
    if me is not None and (not isinstance(me, str) or not SECRET_RE.match(me)):
        return "me must be 32 to 64 lowercase hex digits"
    for k in ("friends", "priority"):
        v = req.get(k)
        if v is not None and (not isinstance(v, list) or len(v) > MAX_FRIENDS):
            return f"{k} must be a list of at most {MAX_FRIENDS} ids"
    if req.get("name") is not None and not isinstance(req.get("name"), str):
        return "name must be text"
    return ""


TOKEN_RE = re.compile(r"^[0-9a-f]{8,64}$")
PROBE_TIMEOUT = 4


def validate_probe(req):
    port = req.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        return "port must be a number from 1024 to 65535"
    if not isinstance(req.get("token"), str) or not TOKEN_RE.match(req["token"]):
        return "token must be 8 to 64 hex digits"
    return ""


async def probe(ip, port, token):
    """Connect back to the asking player's own address only (never anyone else's), and check that the app
    is what answers, so a player can learn whether their router lets other players in."""
    w = None
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), PROBE_TIMEOUT)
        line = await asyncio.wait_for(r.readline(), PROBE_TIMEOUT)
        return line.strip() == token.encode()
    except (OSError, asyncio.TimeoutError, ValueError):
        return False
    finally:
        if w:
            w.close()


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


def peer_view(other, lane, me=None):
    """What one player learns about the other. The IP address is only shared on the native lane, where
    the emulators connect directly and need it; stream matches go through Parsec or our relay instead.
    The player id is only shown to a mutual friend, never to a stranger."""
    v = {**(other.info.get(lane) or {}), "client": other.client}
    if other.name:
        v["name"] = other.name
    if me is not None and mutual(me, other):
        v["friend"] = True
        v["id"] = other.id
    if lane == "native":
        v["ip"] = other.ip
    return v


def mutual(a, b):
    return bool(a.id and b.id and a.id != b.id and a.id in b.friends and b.id in a.friends)


def strangers_ok(x, now):
    return not x.friends_only and now >= x.open_at


def rank(a, b, key, now):
    """Lower is better; None = these two may not be paired (yet)."""
    if "#" in key:
        return 0                                   # an invite or a friend's call: always
    if mutual(a, b):
        return 0 if (b.id in a.priority or a.id in b.priority) else 1
    if strangers_ok(a, now) and strangers_ok(b, now):
        return 2
    return None


def roles(w, other, lane):
    """(host, guest) or None. The player who waited longer hosts, unless only one can, or one asked to."""
    if other.can_host[lane] and w.can_host[lane]:
        return (w, other) if (w.prefer_host and not other.prefer_host) else (other, w)
    if other.can_host[lane]:
        return other, w
    if w.can_host[lane]:
        return w, other
    return None


def find_match(w):
    now = time.monotonic()
    best = None
    for key in w.keys:
        for lane in w.lanes:
            for other in pools.get((key, lane), []):
                if other is w or other.done or not compatible(other, w, lane):
                    continue
                hg = roles(w, other, lane)
                r = rank(w, other, key, now)
                if hg is None or r is None:
                    continue
                cand = (r, other.since, key, lane, hg)
                if best is None or cand[:2] < best[:2]:
                    best = cand
    return None if best is None else best[2:]


def do_match(w, key, lane, hg):
    host, guest = hg
    for x in (host, guest):
        leave_all(x)
        x.done = True
        if x.timer:
            x.timer.cancel()
            x.timer = None
    via = "invite" if "#" in key else "public"
    to_host = {"op": "matched", "v": 1, "lane": lane, "role": "host", "via": via, "peer": peer_view(guest, lane, host)}
    to_guest = {"op": "matched", "v": 1, "lane": lane, "role": "guest", "via": via, "peer": peer_view(host, lane, guest)}
    if lane == "stream" and wants_own_stream(host) and wants_own_stream(guest):
        sess = sessions.create(w.game)
        if sess:    # else: server is full, fall through to the plain match
            to_host["session"] = {"key": sess.keys["host"]}
            to_guest["session"] = {"key": sess.keys["guest"], "play": f"/play?t={sess.keys['guest']}"}
            if TLS:
                to_host["session"]["web"] = to_guest["session"]["web"] = "https"
    for x, msg in ((host, to_host), (guest, to_guest)):
        try:
            send(x, msg)
        except Exception:
            pass
    print(f"matched on {lane} ({w.game}, {via}{', friends' if mutual(host, guest) else ''}) host={host.client} guest={guest.client}", flush=True)


def retry(w):
    """A player's friends-only head start just ended: try again, now with strangers allowed."""
    w.timer = None
    if w.done:
        return
    m = find_match(w)
    if m:
        do_match(w, *m)


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
    ip = (writer.get_extra_info("peername") or [""])[0]
    counted = False
    try:
        try:
            line = await asyncio.wait_for(reader.readline(), FIRST_LINE_TIMEOUT)
        except (asyncio.TimeoutError, asyncio.LimitOverrunError, ValueError):
            return
        if not line:
            return
        try:
            req = json.loads(line)
        except ValueError:
            req = None
        if isinstance(req, dict) and req.get("op") == "probe":
            problem = validate_probe(req) or admit(ip, asyncio.get_running_loop().time())
            if problem:
                reply = {"op": "error", "msg": problem}
            else:
                reply = {"op": "probe", "v": 1, "port": req["port"], "open": await probe(ip, req["port"], req["token"]), "ip": ip}
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
            return
        if isinstance(req, dict) and req.get("op") == "hello":
            await presence(reader, writer, req, ip)
            return
        problem = validate(req) or admit(ip, asyncio.get_running_loop().time())
        if problem:
            writer.write((json.dumps({"op": "error", "msg": problem}) + "\n").encode())
            await writer.drain()
            return
        w = Waiter(writer, req)
        open_by_ip[ip] = open_by_ip.get(ip, 0) + 1
        counted = True
        m = find_match(w)
        if m:
            do_match(w, *m)
            await writer.drain()
            return
        for key in w.keys:
            for lane in w.lanes:
                pools.setdefault((key, lane), []).append(w)
        if w.open_at > time.monotonic():   # friends get first go; after this, strangers are allowed too
            w.timer = asyncio.get_running_loop().call_later(w.open_at - time.monotonic() + 0.01, retry, w)
        print(f"waiting: {w.game} lanes={w.lanes} client={w.client}" + (" (invite)" if any("#" in k for k in w.keys) else ""), flush=True)
        ping = asyncio.create_task(pinger(w))
        while await reader.read(1024):   # until the client cancels (closes); anything it sends is ignored
            pass
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        if counted:
            open_by_ip[ip] -= 1
            if open_by_ip[ip] <= 0:
                open_by_ip.pop(ip, None)
        if ping:
            ping.cancel()
        if w:
            w.done = True
            leave_all(w)
            if w.timer:
                w.timer.cancel()
        writer.close()


# ----------------------------------------------------------------- friends online
class Presence:
    def __init__(self, writer, req, ip):
        self.writer, self.ip = writer, ip
        self.id = player_id(req["me"])
        self.name = clean_name(req.get("name")) or "Player"
        self.friends = id_set(req.get("friends"))
        self.state, self.game = "idle", ""
        self.call_times = []
        self.closed = False


def p_mutual(a, b):
    return a.id != b.id and a.id in b.friends and b.id in a.friends


def p_send(p, msg):
    if p.closed:
        return
    try:
        p.writer.write((json.dumps(msg) + "\n").encode())
    except Exception:
        p.closed = True


def push_friends(p):
    """Tell p which of its friends are online now. Only mutual friends, and only name, state and game."""
    lst = []
    for fid in sorted(p.friends):
        q = online.get(fid)
        if q and p_mutual(p, q):
            lst.append({"id": q.id, "name": q.name, "state": q.state, "game": q.game})
    p_send(p, {"op": "friends", "v": 1, "online": lst})


def tell_friends(p):
    for fid in p.friends:
        q = online.get(fid)
        if q and p_mutual(p, q):
            push_friends(q)


def validate_hello(req):
    if not isinstance(req.get("me"), str) or not SECRET_RE.match(req["me"]):
        return "hello needs me: 32 to 64 lowercase hex digits"
    return validate_social(req)


def prune_calls(now):
    for k in [k for k, v in pending_calls.items() if v[2] < now]:
        pending_calls.pop(k, None)


def on_presence_msg(p, m):
    now = time.monotonic()
    op = m.get("op")
    if op == "status":
        st, game = m.get("state"), m.get("game") or ""
        if st in STATES and isinstance(game, str) and (game == "" or GAME_RE.match(game)):
            p.state, p.game = st, game
            tell_friends(p)
    elif op == "call":
        to, game, kind, cid = m.get("to"), m.get("game"), m.get("kind"), m.get("call")
        if not (isinstance(to, str) and ID_RE.match(to) and isinstance(game, str) and GAME_RE.match(game)
                and kind in ("play", "watch") and isinstance(cid, str) and CALL_RE.match(cid)):
            return p_send(p, {"op": "call_failed", "call": cid if isinstance(cid, str) else "", "msg": "bad call"})
        p.call_times = [t for t in p.call_times if now - t < 60]
        if len(p.call_times) >= CALLS_PER_MIN:
            return p_send(p, {"op": "call_failed", "call": cid, "msg": "too many calls; wait a minute"})
        p.call_times.append(now)
        q = online.get(to)
        if not q or not p_mutual(p, q):
            return p_send(p, {"op": "call_failed", "call": cid, "msg": "not online"})
        prune_calls(now)
        if len(pending_calls) > 50000:
            return p_send(p, {"op": "call_failed", "call": cid, "msg": "the matchmaker is busy"})
        pending_calls[(p.id, q.id, cid)] = (game, kind, now + CALL_SECONDS)
        p_send(q, {"op": "call", "v": 1, "from": p.id, "name": p.name, "game": game, "kind": kind, "call": cid})
    elif op == "answer":
        caller, cid = m.get("to"), m.get("call")
        if not (isinstance(caller, str) and isinstance(cid, str)):
            return
        prune_calls(now)
        entry = pending_calls.pop((caller, p.id, cid), None)
        c = online.get(caller)
        if not entry or not c:
            return
        game, kind, _ = entry
        if m.get("ok") is True:
            code = secrets.token_hex(8)
            p_send(c, {"op": "start", "v": 1, "call": cid, "code": code, "game": game, "kind": kind, "as": "caller",
                       "with": {"id": p.id, "name": p.name}})
            p_send(p, {"op": "start", "v": 1, "call": cid, "code": code, "game": game, "kind": kind, "as": "callee",
                       "with": {"id": c.id, "name": c.name}})
        else:
            p_send(c, {"op": "declined", "call": cid, "name": p.name})
    elif op == "cancel_call":
        to, cid = m.get("to"), m.get("call")
        if isinstance(to, str) and isinstance(cid, str) and pending_calls.pop((p.id, to, cid), None):
            q = online.get(to)
            if q:
                p_send(q, {"op": "call_cancelled", "call": cid, "from": p.id})


async def presence(reader, writer, req, ip):
    problem = validate_hello(req) or admit(ip, asyncio.get_running_loop().time())
    if not problem and presence_by_ip.get(ip, 0) >= MAX_PRESENCE_PER_IP:
        problem = "too many connections from your address"
    if not problem and len(online) >= MAX_PRESENCE:
        problem = "the matchmaker is busy; try again in a moment"
    if problem:
        writer.write((json.dumps({"op": "error", "msg": problem}) + "\n").encode())
        await writer.drain()
        return
    p = Presence(writer, req, ip)
    old = online.get(p.id)
    if old:                                   # the same player opened a new connection: drop the old one
        old.closed = True
        old.writer.close()
    online[p.id] = p
    presence_by_ip[ip] = presence_by_ip.get(ip, 0) + 1
    p_send(p, {"op": "hello", "v": 1, "id": p.id})
    push_friends(p)
    tell_friends(p)
    last = time.monotonic()
    try:
        while not p.closed:
            try:
                line = await asyncio.wait_for(reader.readline(), PING_SECONDS)
            except asyncio.TimeoutError:
                if time.monotonic() - last > PING_SECONDS * 3:
                    break
                p_send(p, {"op": "ping"})
                await writer.drain()
                continue
            if not line:
                break
            last = time.monotonic()
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if isinstance(m, dict):
                on_presence_msg(p, m)
                await writer.drain()
    except (ConnectionError, ValueError, asyncio.LimitOverrunError, asyncio.IncompleteReadError):
        pass
    finally:
        p.closed = True
        presence_by_ip[ip] -= 1
        if presence_by_ip[ip] <= 0:
            presence_by_ip.pop(ip, None)
        if online.get(p.id) is p:
            online.pop(p.id, None)
            tell_friends(p)
        for k in [k for k in pending_calls if p.id in (k[0], k[1])]:
            pending_calls.pop(k, None)


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


def friend_page(host_header, tcp_port, fid, name):
    """HTML for a friend link. The id and name are validated and escaped; nothing is stored."""
    host = (host_header or "").rsplit(":", 1)[0] if host_header and not host_header.endswith("]") else (host_header or "")
    name = clean_name(name) or "A player"
    if not (HOST_RE.match(host) and ID_RE.match(fid or "")):
        return "<h1>Retro-Netplay</h1><p>This friend link looks incomplete or damaged. Ask your friend to send it again.</p>"
    deep = "netplay://friend?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in
                                          (("i", fid), ("n", name), ("host", host), ("port", tcp_port)))
    return (f"<h1>{html.escape(name)} wants to be friends</h1>"
            f"<p><a class='btn' href='{html.escape(deep, quote=True)}'>Add {html.escape(name)} in Retro-Netplay</a></p>"
            "<p>Friends get matched with each other first, and you choose what each friend may do. "
            "You both have to add each other.</p>"
            "<p>The button does nothing? In the app open <b>Friends</b>, choose <b>Add a friend</b> and paste this page's address.</p>")


def tls_context():
    """Optional https for the web port (stream pages and signaling). Set NETPLAY_TLS_CERT and
    NETPLAY_TLS_KEY to a certificate and key, e.g. from Let's Encrypt, for the server's domain name."""
    cert, key = os.environ.get("NETPLAY_TLS_CERT", ""), os.environ.get("NETPLAY_TLS_KEY", "")
    if not (cert and key):
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


TLS = tls_context()
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
    hsts = "Strict-Transport-Security: max-age=31536000\r\n" if TLS else ""
    return (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(data)}\r\n{hsts}"
            f"X-Frame-Options: SAMEORIGIN\r\n"
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
            elif u.path == "/friend":
                out = respond("200 OK", page(friend_page(headers.get("host", ""), tcp_port, first("i"), first("n"))))
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
    srv = await asyncio.start_server(handle, "0.0.0.0", port, limit=MAX_LINE)
    print(f"matchmaker on :{port}", flush=True)
    if http_port:
        try:
            await asyncio.start_server(lambda r, w: http_handle(r, w, port), "0.0.0.0", http_port, ssl=TLS)
            print(f"invite page on :{http_port}" + (" (https)" if TLS else ""), flush=True)
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
