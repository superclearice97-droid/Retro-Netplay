"""Stream sessions: the little signaling relay that lets two peers set up a WebRTC stream.

The matchmaker creates one Session when two players match on the stream lane and both asked for
the built-in streamer (info.stream.backend == "own"). Each side gets its OWN secret key. A key
only works for its own role, so a guest can never post as the host. Messages are small JSON
objects (offer / answer / ice / hello / bye) relayed through two in-memory queues.

Each role reaches its queue one way only (`via`): "tcp" = a stream connection on the matchmaker's TCP
port (server.py, the normal way), or "http" = long-poll on the web port. DEPRECATED: "http" exists only
for the current Android app, whose player is a web page; remove it once the Android native player ships.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

SESSION_TTL = 2 * 3600
MAX_SESSIONS = 500
MAX_QUEUE = 200
MAX_BODY = 16384
MAX_WAIT = 25
ROLES = ("host", "guest")
DEFAULT_ICE = [{"urls": ["stun:stun.l.google.com:19302"]}]

sessions = {}   # id -> Session
by_key = {}     # key -> (Session, role)


class Session:
    def __init__(self, game, via=None):
        self.id = secrets.token_urlsafe(8)
        self.via = {r: (via or {}).get(r, "http") for r in ROLES}   # how each role may signal: "tcp" or "http"
        self.conn = {r: None for r in ROLES}    # the open TCP stream connection of each role (a close function)
        self.game = game
        self.created = time.time()
        self.keys = {r: secrets.token_urlsafe(18) for r in ROLES}
        self.q = {r: [] for r in ROLES}
        self.ev = {r: asyncio.Event() for r in ROLES}
        self.closed = False


BYE_GRACE = 60      # seconds a closed session waits to hand its goodbye (with the reason) to the other side


def cleanup(now=None):
    now = time.time() if now is None else now
    for s in [s for s in sessions.values() if now - s.created > SESSION_TTL
              or (s.closed and (not any(s.q.values()) or now - s.closed_at > BYE_GRACE))]:
        drop(s)


def drop(s):
    s.closed = True
    for close in s.conn.values():
        if close:
            close()
    sessions.pop(s.id, None)
    for k in s.keys.values():
        by_key.pop(k, None)
    for e in s.ev.values():
        e.set()     # wake any waiting long-poll so it can notice the session is over


def create(game, via=None):
    cleanup()
    if len(sessions) >= MAX_SESSIONS:
        return None
    s = Session(game, via)
    sessions[s.id] = s
    for r, k in s.keys.items():
        by_key[k] = (s, r)
    return s


def lookup(key, via="http"):
    """(session, role) if this key may signal this way (via "http" or "tcp"), else None."""
    hit = by_key.get(key or "") if isinstance(key, str) else None
    if not hit or hit[0].via[hit[1]] != via:
        return None
    s, role = hit
    expired = time.time() - s.created > SESSION_TTL
    # A session that was closed with a goodbye still hands that goodbye over before it disappears.
    if expired or (s.closed and not s.q[role]):
        drop(s)
        return None
    return s, role


def other(role):
    return "guest" if role == "host" else "host"


def push(s, from_role, msg):
    """Queue msg for the other side. Returns an error string, or '' on success."""
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str) or len(msg["type"]) > 24:
        return "message must be an object with a short string 'type'"
    if len(s.q[other(from_role)]) >= MAX_QUEUE:
        return "too many unread messages"
    if msg["type"] == "bye":
        s.q[other(from_role)].append(msg)
        s.ev[other(from_role)].set()
        s.closed, s.closed_at = True, time.time()     # nothing more will be accepted; cleanup() removes it
        return ""
    s.q[other(from_role)].append(msg)
    s.ev[other(from_role)].set()
    return ""


async def poll(s, role, wait):
    wait = max(0.0, min(float(wait), MAX_WAIT))
    if not s.q[role] and wait and not s.closed:
        s.ev[role].clear()
        try:
            await asyncio.wait_for(s.ev[role].wait(), wait)
        except asyncio.TimeoutError:
            pass
    out, s.q[role] = s.q[role], []
    return out


def static_ice():
    """STUN by default. NETPLAY_ICE_JSON = a fixed JSON list instead (older way to add a relay)."""
    raw = os.environ.get("NETPLAY_ICE_JSON", "")
    if raw:
        try:
            v = json.loads(raw)
            if isinstance(v, list):
                return v
        except ValueError:
            pass
    return DEFAULT_ICE


# ----------------------------------------------------------------- TURN relay (for strict routers)
# The matchmaker never stores or ships a relay password. It shares one secret with the relay (coturn's
# static-auth-secret) and hands each player a password that works for TURN_TTL seconds only
# ("TURN REST API" style: username = "<expiry time>:<label>", password = base64(HMAC-SHA1(secret, username))).
TURN_TTL = SESSION_TTL + 3600
TURN = {"urls": [], "secret": ""}
TURN_URL_RE = re.compile(r"^turns?:[A-Za-z0-9.\-\[\]:]{1,255}(\?transport=(udp|tcp))?$")


def configure_turn(urls, secret):
    """Use this relay from now on. Bad addresses are dropped; no secret = no relay."""
    TURN["urls"] = [u for u in (urls or []) if isinstance(u, str) and TURN_URL_RE.match(u)][:4]
    TURN["secret"] = secret if isinstance(secret, str) and len(secret) >= 16 else ""
    return bool(TURN["urls"] and TURN["secret"])


def turn_credentials(label, now=None, secret=None):
    """(username, password) valid until now + TURN_TTL."""
    user = f"{int(now if now is not None else time.time()) + TURN_TTL}:{label}"
    mac = hmac.new((secret or TURN["secret"]).encode(), user.encode(), hashlib.sha1).digest()
    return user, base64.b64encode(mac).decode()


def has_relay(servers):
    return any(str(u).startswith(("turn:", "turns:")) for s in servers if isinstance(s, dict)
               for u in ([s.get("urls")] if isinstance(s.get("urls"), str) else (s.get("urls") or [])))


def ice_servers(role=""):
    """What each player's WebRTC uses to find a way through routers: STUN, plus the relay if one is set up."""
    out = list(static_ice())
    if TURN["urls"] and TURN["secret"]:
        user, cred = turn_credentials(f"netplay-{role or 'player'}")
        out.append({"urls": list(TURN["urls"]), "username": user, "credential": cred})
    return out
