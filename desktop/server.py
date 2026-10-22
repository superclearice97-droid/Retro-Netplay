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
import socket
import ssl
import time
import sys
from urllib.parse import parse_qs, quote, urlsplit

import sessions

PING_SECONDS = 20          # keeps phones on flaky mobile networks from lingering as ghosts
LANES = ("native", "stream")
CODE_RE = re.compile(r"^[A-Za-z0-9]{4,32}$")
INVITE_CODE_RE = re.compile(r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$")   # the codes the apps make for invites
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
call_hosts = {}            # private-room code of an accepted call -> (callee id, expiry): the one who said yes hosts


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
        # an app that offers the built-in stream but only knows the old web page way (Android app versions before
        # its own player, desktop apps before the player window) can't stream any more: it still plays netplay
        self.outdated = "stream" in self.lanes and wants_own_stream(self) and not tcp_sig(self)
        if self.outdated:
            self.lanes.remove("stream")
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
        if me is not None and is_local(other.ip):
            if is_loopback(other.ip):
                # the host runs on the matchmaker's own computer: the guest reaches it at the address the guest
                # used for the matchmaker (the PC's Wi-Fi address for a phone at home), not at 127.0.0.1
                here = (me.writer.get_extra_info("sockname") or [""])[0]
                if here and not is_loopback(here):
                    v["ip"] = here
            if not is_local(me.ip):
                v["at_matchmaker"] = True     # host is behind the matchmaker's router: use the matchmaker's address
    return v


def is_loopback(ip):
    return ip == "::1" or ip.startswith("127.") or ip.startswith("::ffff:127.")


def is_local(ip):
    """Loopback or a home-network (private) address."""
    if is_loopback(ip):
        return True
    return bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|169\.254\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)", ip))


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


def tcp_sig(w):
    """Does this player's app signal over the TCP stream connection (info.stream.sig == "tcp")? Apps without it
    are older versions that only knew the (removed) web page way."""
    st = w.info.get("stream")
    return isinstance(st, dict) and st.get("sig") == "tcp"


def roles(w, other, lane, key=""):
    """(host, guest) or None. See _roles."""
    return _roles(w, other, lane, key)


def _roles(w, other, lane, key=""):
    """(host, guest) or None. The player who waited longer hosts, unless only one can, or one asked to.
    In a friend's join, the friend who accepted hosts whenever they can."""
    code = key.split("#", 1)[1] if "#" in key else ""
    callee = call_hosts.get(code, ("", 0))[0] if code else ""
    if callee:
        for a, b in ((w, other), (other, w)):
            if a.id == callee and a.can_host[lane]:
                return a, b
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
                hg = roles(w, other, lane, key)
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
        # both sides signal over the TCP stream connection (apps that can't lost the stream lane in Waiter)
        sess = sessions.create(w.game)
        if sess:    # else: server is full, fall through to the plain match
            for role, msg in (("host", to_host), ("guest", to_guest)):
                msg["session"] = {"key": sess.keys[role], "sig": "tcp"}
    for x, msg in ((host, to_host), (guest, to_guest)):
        try:
            send(x, msg)
        except Exception:
            pass
    print(f"matched on {lane} ({w.game}, {via}{', friends' if mutual(host, guest) else ''}) host={host.client} guest={guest.client}", flush=True)


def dead_end(w):
    """(other, reason) if w is in a private room with someone it can never be paired with, else None."""
    for key in w.keys:
        if "#" not in key:
            continue
        others = {o for lane in LANES for o in pools.get((key, lane), []) if o is not w and not o.done}
        for o in others:
            common = [l for l in LANES if l in w.lanes and l in o.lanes]
            if any(compatible(o, w, l) and roles(w, o, l, key) for l in common):
                continue
            if (w.outdated or o.outdated) and "native" not in common:
                why = ("Your apps can't stream to each other: one of you has an older Retro-Netplay. "
                       "Update the app on both devices, then try again.")
            elif not common:
                why = ("You can't play this game together: one of you can only join a stream, and the other can't host "
                       "one or play it with emulator netplay.")
            elif "stream" in common and not ("native" in common):
                why = ("Neither of you can host a stream of this game. The computer playing it needs the streaming tools "
                       "(Settings > Streaming check), or both need emulator netplay for this game.")
            else:
                why = "Neither of you can host this game right now (check the router port, or try the other person hosting)."
            return o, why
    return None


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
        if len(line) > MAX_LINE:
            return
        try:
            req = json.loads(line)
        except ValueError:
            req = None
        if isinstance(req, dict) and req.get("op") == "starttls":
            # encrypt this connection (when the operator set up a certificate), then it starts over with its real first line
            writer.write((json.dumps({"op": "starttls", "v": 1, "ok": TLS is not None}) + "\n").encode())
            await writer.drain()
            if TLS is not None:
                await asyncio.wait_for(start_tls(writer, TLS), FIRST_LINE_TIMEOUT)
            line = await asyncio.wait_for(reader.readline(), FIRST_LINE_TIMEOUT)
            if not line or len(line) > MAX_LINE:
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
        if isinstance(req, dict) and req.get("op") == "whoami":
            # "what address do you see for me?" so a player can share it with friends
            writer.write((json.dumps({"op": "whoami", "v": 1, "ip": ip, "app": "retro-netplay", "since": int(START),
                                      "id": INSTANCE, "name": computer_name()}) + "\n").encode())
            await writer.drain()
            return
        if isinstance(req, dict) and req.get("op") == "invite":
            # "which game is waiting under this invite code?" so a friend can type the code instead of a link
            code = str(req.get("code", "")).upper()
            problem = admit(ip, asyncio.get_running_loop().time())
            game = "" if problem or not INVITE_CODE_RE.match(code) else next(
                (w.game for (room, _lane), lst in pools.items() if room.endswith("#" + code) for w in lst if not w.done), "")
            reply = {"op": "error", "msg": problem} if problem else {"op": "invite", "v": 1, "found": bool(game), "game": game}
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
            return
        if isinstance(req, dict) and req.get("op") == "stream":
            await stream_conn(reader, writer, req, ip)
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
        if not w.lanes:     # it only offered the built-in stream, which its version can't do any more
            send(w, {"op": "error", "why": "old_app", "msg": "This version of Retro-Netplay can't join or host streams "
                     "any more. Update the app (the stream now plays in its own player), then try again."})
            await writer.drain()
            return
        m = find_match(w)
        if m:
            do_match(w, *m)
            await writer.drain()
            return
        stuck = dead_end(w)
        if stuck:
            # a private room (a friend's join, an invite) where the two can never connect: say so now, not never
            other, why = stuck
            for x in (w, other):
                try:
                    send(x, {"op": "error", "msg": why, "why": "no_common_lane"})
                except Exception:
                    pass
            other.done = True
            leave_all(other)
            other.writer.close()
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
    except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError, OSError):   # (OSError: a TLS handshake failed)
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


# ----------------------------------------------------------------- stream signaling over TCP
# A matched player opens one more connection, {"op":"stream","key":...}, and the two apps swap their WebRTC setup
# messages through it (see sessions.py). The key only works for its own role and its own session.
STREAM_LINE = sessions.MAX_BODY + 512   # one setup message per line (an offer is a few KB)
SIG_PER_MIN = 300                       # setup messages one connection may send per minute (a setup takes ~20)
STREAM_IDLE = 70                        # seconds of silence before a stream connection is dropped (apps ping every 20)


def s_send(writer, msg):
    writer.write((json.dumps(msg) + "\n").encode())


async def stream_pump(s, role, writer):
    """Hand this role every message the other side sends, as it arrives; close once the session is over."""
    try:
        while True:
            if time.time() - s.created > sessions.SESSION_TTL:
                s_send(writer, {"op": "error", "msg": "This stream session is over (sessions last 2 hours). "
                                                     "Press Join again to keep playing.", "why": "expired"})
                await writer.drain()
                sessions.drop(s)
                return
            msgs = await sessions.poll(s, role, PING_SECONDS)
            for m in msgs:
                s_send(writer, {"op": "sig", "msg": m})
            if not msgs:
                s_send(writer, {"op": "ping"})
            await writer.drain()
            if s.closed and not s.q[role]:
                return                      # the goodbye was handed over: nothing more will come
    except (ConnectionError, RuntimeError):
        pass
    finally:
        writer.close()


async def stream_conn(reader, writer, req, ip):
    problem = admit(ip, asyncio.get_running_loop().time())
    hit = None if problem else sessions.lookup(req.get("key"))
    if not problem and not hit:
        problem = "This stream has ended, or its key is wrong."
    if problem:
        s_send(writer, {"op": "error", "msg": problem, "why": "bad_key" if problem.startswith("This stream") else "busy"})
        await writer.drain()
        return
    s, role = hit
    if s.conn[role]:
        s.conn[role]()                      # the same player reconnected: the newest connection wins
    pump = asyncio.ensure_future(stream_pump(s, role, writer))

    def close():
        pump.cancel()
        writer.close()
    s.conn[role] = close
    open_by_ip[ip] = open_by_ip.get(ip, 0) + 1
    ice = sessions.ice_servers(role)
    s_send(writer, {"op": "stream", "v": 1, "role": role, "game": s.game, "iceServers": ice,
                    "relay": sessions.has_relay(ice)})
    times, last = [], time.monotonic()
    try:
        await writer.drain()
        while not pump.done():
            try:
                line = await asyncio.wait_for(reader.readline(), PING_SECONDS)
            except asyncio.TimeoutError:
                if time.monotonic() - last > STREAM_IDLE:
                    break                   # the app went quiet (it pings every 20 s): free the slot
                continue
            if not line:
                break
            last = time.monotonic()
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if not isinstance(m, dict) or m.get("op") != "sig":
                continue                    # "ping" and anything unknown: nothing to do
            now = time.monotonic()
            times = [t for t in times if now - t < 60] + [now]
            if len(times) > SIG_PER_MIN:
                s_send(writer, {"op": "error", "msg": "too many messages", "why": "rate"})
                break
            msg = m.get("msg")
            err = ("message too large" if len(json.dumps(msg)) > sessions.MAX_BODY else "") or sessions.push(s, role, msg)
            if err:
                s_send(writer, {"op": "error_soft", "msg": err})
            await writer.drain()
    except (ConnectionError, ValueError, asyncio.LimitOverrunError, asyncio.IncompleteReadError):
        pass
    finally:
        open_by_ip[ip] -= 1
        if open_by_ip[ip] <= 0:
            open_by_ip.pop(ip, None)
        if s.conn[role] is close:
            s.conn[role] = None
        close()


def load_turn():
    """The relay for strict routers, if whoever runs this matchmaker set one up (see README.txt, "TURN relay"):
    NETPLAY_TURN_URLS (comma-separated turn:/turns: addresses) and NETPLAY_TURN_SECRET, or the same as
    {"urls": [...], "secret": "..."} in turn.json in the server's data folder. Nothing is set up by default."""
    urls = [u.strip() for u in os.environ.get("NETPLAY_TURN_URLS", "").replace(" ", ",").split(",") if u.strip()]
    secret = os.environ.get("NETPLAY_TURN_SECRET", "")
    if not (urls and secret):
        try:
            with open(os.path.join(data_dir(), "turn.json")) as f:
                raw = json.load(f)
            urls, secret = raw.get("urls") or [], raw.get("secret") or ""
        except (OSError, ValueError, AttributeError):
            return False
    ok = sessions.configure_turn(urls, secret)
    print("relay for strict routers: " + (", ".join(sessions.TURN["urls"]) if ok else "set up wrong (needs turn: addresses "
          "and a secret of 16+ characters)"), flush=True)
    return ok


# ----------------------------------------------------------------- friends online
class Presence:
    def __init__(self, writer, req, ip):
        self.writer, self.ip = writer, ip
        self.id = player_id(req["me"])
        self.name = clean_name(req.get("name")) or "Player"
        self.friends = id_set(req.get("friends"))
        self.state, self.game = "idle", ""
        self.call_times = []
        self.ask_times = []
        self.hidden = bool(req.get("hidden"))      # still reachable for requests, just not shown online
        self.can = {}
        self.closed = False
        self.code = ""


def p_mutual(a, b):
    return a.id != b.id and a.id in b.friends and b.id in a.friends


# ----------------------------------------------------------------- short friend codes
# Each player gets a 6-character code on this matchmaker (shown as K7M-4QZ). The server keeps only
# code -> player id and the name they chose, so friends can add each other by code even when offline.
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
SHORT_RE = re.compile(r"^[2-9A-HJ-NP-Z]{6}$")
REQUESTS_PER_MIN = 20
REQUEST_DAYS = 14
MAX_PENDING = 50
codes = {}             # code -> {"id", "name"}
code_of_id = {}        # id -> code
pending = {}           # id -> {from id: {"op":..., ...message, "t": time}}
_codes_dirty = False


def data_dir():
    d = os.environ.get("NETPLAY_DATA") or os.path.join(os.path.expanduser("~"), ".netplay-matchmaker", "server")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def load_codes():
    try:
        with open(os.path.join(data_dir(), "friend-codes.json")) as f:
            raw = json.load(f)
        for c, v in raw.items():
            if SHORT_RE.match(c) and isinstance(v, dict) and ID_RE.match(str(v.get("id", ""))):
                codes[c] = {"id": v["id"], "name": clean_name(v.get("name")) or "Player"}
                code_of_id[v["id"]] = c
    except (OSError, ValueError):
        pass


def save_codes():
    global _codes_dirty
    if not _codes_dirty:
        return
    _codes_dirty = False
    try:
        path = os.path.join(data_dir(), "friend-codes.json")
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(codes, f)
        os.replace(tmp, path)
    except OSError:
        pass


def short_code(pid, salt=0):
    h = hashlib.sha256(f"code:{pid}:{salt}".encode()).digest()
    n = int.from_bytes(h[:4], "big") >> 2           # 30 bits
    return "".join(CODE_ALPHABET[(n >> (5 * i)) & 31] for i in range(6))


def assign_code(pid, name):
    """The player's code on this server (the same every time), and remember their latest name."""
    global _codes_dirty
    c = code_of_id.get(pid)
    if not c:
        salt = 0
        while True:
            c = short_code(pid, salt)
            if c not in codes:
                break
            salt += 1
        code_of_id[pid] = c
        codes[c] = {"id": pid, "name": name}
        _codes_dirty = True
    elif codes[c]["name"] != name:
        codes[c]["name"] = name
        _codes_dirty = True
    return c


def pretty_code(c):
    return c[:3] + "-" + c[3:]


def normal_code(text):
    return re.sub(r"[\s-]", "", str(text or "")).upper()


def deliver(to, msg):
    """Send now if they're online, else keep it (in memory) until they connect."""
    q = online.get(to)
    if q and not q.closed:
        p_send(q, msg)
        return
    box = pending.setdefault(to, {})
    if len(box) >= MAX_PENDING and msg["from"] not in box:
        return
    box[msg["from"]] = {**msg, "t": time.time()}


def flush_pending(p):
    now = time.time()
    for frm, msg in list(pending.pop(p.id, {}).items()):
        if now - msg.get("t", now) < REQUEST_DAYS * 86400:
            p_send(p, {k: v for k, v in msg.items() if k != "t"})


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
        if q and p_mutual(p, q) and not q.hidden:
            lst.append({"id": q.id, "name": q.name, "state": q.state, "game": q.game, "code": pretty_code(q.code), "can": q.can})
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
    for k in [k for k, v in call_hosts.items() if v[1] < now]:
        call_hosts.pop(k, None)


def on_presence_msg(p, m):
    now = time.monotonic()
    op = m.get("op")
    if op == "status":
        st, game = m.get("state"), m.get("game") or ""
        if st in STATES and isinstance(game, str) and (game == "" or GAME_RE.match(game)):
            p.state, p.game = st, game
            can = m.get("can")
            if isinstance(can, dict):     # what this player can host now: lets friends see if joining will work
                p.can = {k: bool(can.get(k)) for k in ("native", "stream")}
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
            call_hosts[code] = (p.id, now + 10 * 60)
            p_send(c, {"op": "start", "v": 1, "call": cid, "code": code, "game": game, "kind": kind, "as": "caller",
                       "with": {"id": p.id, "name": p.name}})
            p_send(p, {"op": "start", "v": 1, "call": cid, "code": code, "game": game, "kind": kind, "as": "callee",
                       "with": {"id": c.id, "name": c.name}})
        else:
            why = m.get("msg")
            msg = {"op": "declined", "call": cid, "name": p.name}
            if isinstance(why, str) and why.isprintable():
                msg["msg"] = why[:160]
            p_send(c, msg)
    elif op == "set_friends":
        # the friend list changed (request sent, accepted, removed): no need to reconnect
        old, p.friends = p.friends, id_set(m.get("friends"))
        push_friends(p)
        for fid in old | p.friends:
            q = online.get(fid)
            if q and q is not p:
                push_friends(q)
    elif op == "cancel_call":
        to, cid = m.get("to"), m.get("call")
        if isinstance(to, str) and isinstance(cid, str) and pending_calls.pop((p.id, to, cid), None):
            q = online.get(to)
            if q:
                p_send(q, {"op": "call_cancelled", "call": cid, "from": p.id})
    elif op in ("lookup", "friend_request", "friend_accept"):
        p.ask_times = [t for t in p.ask_times if now - t < 60]
        if len(p.ask_times) >= REQUESTS_PER_MIN:
            return p_send(p, {"op": "error_soft", "about": op, "msg": "too many tries; wait a minute"})
        p.ask_times.append(now)
        if op == "lookup":
            c = normal_code(m.get("code"))
            hit = codes.get(c) if SHORT_RE.match(c) else None
            reply = {"op": "lookup", "v": 1, "code": pretty_code(c) if SHORT_RE.match(c) else str(m.get("code", ""))[:16],
                     "found": bool(hit)}
            if hit:
                reply.update(id=hit["id"], name=hit["name"], self=hit["id"] == p.id)
            return p_send(p, reply)
        to = m.get("to")
        if not (isinstance(to, str) and ID_RE.match(to)) or to == p.id or to not in code_of_id:
            return p_send(p, {"op": "error_soft", "about": op, "msg": "no player with that code"})
        deliver(to, {"op": op, "v": 1, "from": p.id, "name": p.name, "code": pretty_code(p.code)})
        if op == "friend_request":
            p_send(p, {"op": "request_sent", "to": to})


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
    p.code = assign_code(p.id, p.name)
    save_codes()
    p_send(p, {"op": "hello", "v": 1, "id": p.id, "code": pretty_code(p.code)})
    flush_pending(p)
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
            "<p>The button does nothing? In the app choose <b>Enter code</b> and paste this "
            "page's address.</p>")


def friend_page(host_header, tcp_port, fid, name, code=""):
    """HTML for a friend link (short code, or an older id link). Validated and escaped; nothing is stored."""
    host = (host_header or "").rsplit(":", 1)[0] if host_header and not host_header.endswith("]") else (host_header or "")
    name = clean_name(name) or "A player"
    c = normal_code(code)
    if not HOST_RE.match(host) or not (SHORT_RE.match(c) or ID_RE.match(fid or "")):
        return "<h1>Retro-Netplay</h1><p>This friend link looks incomplete or damaged. Ask your friend to send it again.</p>"
    key = ("c", c) if SHORT_RE.match(c) else ("i", fid)
    deep = "netplay://friend?" + "&".join(f"{k}={quote(str(v), safe='')}" for k, v in
                                          (key, ("n", name), ("host", host), ("port", tcp_port)))
    shown = f" (friend code {pretty_code(c)})" if SHORT_RE.match(c) else ""
    return (f"<h1>{html.escape(name)} wants to be friends</h1>"
            f"<p><a class='btn' href='{html.escape(deep, quote=True)}'>Add {html.escape(name)} in Retro-Netplay</a></p>"
            f"<p>Or open Retro-Netplay, go to <b>Friends</b> and type their code{html.escape(shown)}.</p>"
            "<p>Friends get matched with each other first, and you choose what each friend may do.</p>")


async def start_tls(writer, ctx):
    """Switch an accepted connection to TLS in place (StreamWriter.start_tls is Python 3.11+)."""
    if hasattr(writer, "start_tls"):
        return await writer.start_tls(ctx)
    loop = asyncio.get_running_loop()
    writer._transport = await loop.start_tls(writer.transport, writer.transport.get_protocol(), ctx, server_side=True)


def tls_context():
    """Optional encryption: https on the web port, and TLS on the matchmaker port for apps that ask ("starttls").
    Set NETPLAY_TLS_CERT and NETPLAY_TLS_KEY to a certificate and key, e.g. from Let's Encrypt, for the server's
    domain name."""
    cert, key = os.environ.get("NETPLAY_TLS_CERT", ""), os.environ.get("NETPLAY_TLS_KEY", "")
    if not (cert and key):
        return None
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


TLS = tls_context()
def page(body):
    return ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Netplay Matchmaker</title><style>body{font-family:sans-serif;max-width:32rem;margin:2rem auto;padding:0 1rem;line-height:1.5}"
            ".btn{display:inline-block;padding:.9rem 1.4rem;background:#2d7ff9;color:#fff;border-radius:.5rem;text-decoration:none;font-weight:bold}"
            f"</style></head><body>{body}</body></html>")


PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'"


def respond(status, body, ctype="text/html; charset=utf-8", csp=PAGE_CSP):
    data = body if isinstance(body, bytes) else body.encode()
    hsts = "Strict-Transport-Security: max-age=31536000\r\n" if TLS else ""
    return (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(data)}\r\n{hsts}"
            f"X-Frame-Options: SAMEORIGIN\r\n"
            f"Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\n"
            f"Content-Security-Policy: {csp}\r\nConnection: close\r\n\r\n").encode() + data


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
        first = lambda k: (q.get(k) or [""])[0][:200]
        if method != "GET":
            out = respond("405 Method Not Allowed", page("<p>Not allowed.</p>"))
        elif u.path == "/join":
            port = first("p")
            out = respond("200 OK", page(landing_page(headers.get("host", ""),
                          int(port) if port.isdigit() and 0 < int(port) < 65536 else tcp_port,
                          first("g"), first("c"))))
        elif u.path == "/friend":
            out = respond("200 OK", page(friend_page(headers.get("host", ""), tcp_port, first("i"), first("n"), first("c"))))
        else:
            out = respond("200 OK", page("<h1>Netplay Matchmaker</h1><p>The matchmaker is running.</p>"))
    except ValueError:
        out = respond("400 Bad Request", page("<p>Bad request.</p>"))
    try:
        writer.write(out)
        await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


# ----------------------------------------------------------------- finding the matchmaker on a home network
DISCOVER_ASK = b"RETRO-NETPLAY-DISCOVER/1"


# Which matchmaker everyone on a home network uses: the one that has been running longest (then the lowest address).
# Every app applies the same rule to the same answers, so phones and computers all end up on the same one.
START = time.time()
INSTANCE = secrets.token_hex(6)


def computer_name():
    return re.sub(r"[^A-Za-z0-9 ._-]", "", socket.gethostname())[:40] or "Matchmaker"


class Discovery(asyncio.DatagramProtocol):
    """Answers 'is there a matchmaker here?' from devices on the same network (UDP port N+2), so phones and
    other computers find it without anyone typing an address. The answer is only the port numbers and the
    computer's name; it never joins anyone to anything."""

    def __init__(self, port, http_port):
        self.port, self.http_port, self.times = port, http_port, {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.strip() != DISCOVER_ASK:
            return
        now = time.monotonic()
        recent = [t for t in self.times.get(addr[0], []) if now - t < 10]
        if len(recent) >= 20:
            return
        self.times[addr[0]] = recent + [now]
        if len(self.times) > 5000:
            self.times.clear()
        reply = {"app": "retro-netplay", "v": 1, "port": self.port, "web": self.http_port, "name": computer_name(),
                 "since": int(START), "id": INSTANCE}
        try:
            self.transport.sendto(json.dumps(reply).encode(), addr)
        except OSError:
            pass


async def main(port, http_port=None, discovery=True, ready=None):
    global START
    START = time.time()
    http_port = port + 1 if http_port is None else http_port
    load_codes()
    load_turn()
    if discovery:
        try:
            await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: Discovery(port, http_port), local_addr=("0.0.0.0", port + 2))
            print(f"home-network discovery on udp :{port + 2}", flush=True)
        except OSError as e:
            print(f"home-network discovery off ({e})", flush=True)
    srv = await asyncio.start_server(handle, "0.0.0.0", port, limit=STREAM_LINE)
    print(f"matchmaker on :{port}", flush=True)
    if ready:
        ready()
    if http_port:
        try:
            await asyncio.start_server(lambda r, w: http_handle(r, w, port), "0.0.0.0", http_port, ssl=TLS)
            print(f"invite page on :{http_port}" + (" (https)" if TLS else ""), flush=True)
        except OSError as e:
            print(f"invite page disabled ({e})", flush=True)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(main(int(args[0]) if args else 7777, int(args[1]) if len(args) > 1 else None))
