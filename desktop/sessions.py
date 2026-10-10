"""Stream sessions: the little signaling relay that lets two peers set up a WebRTC stream.

The matchmaker creates one Session when two players match on the stream lane and both asked for
the built-in streamer (info.stream.backend == "own"). Each side gets its OWN secret key. A key
only works for its own role, so a guest can never post as the host. Messages are small JSON
objects (offer / answer / ice / hello / bye) relayed through two in-memory queues; delivery is a
long-poll over plain HTTP, so there is nothing to install on the guest's side but a browser.
"""
import asyncio
import json
import os
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
    def __init__(self, game):
        self.id = secrets.token_urlsafe(8)
        self.game = game
        self.created = time.time()
        self.keys = {r: secrets.token_urlsafe(18) for r in ROLES}
        self.q = {r: [] for r in ROLES}
        self.ev = {r: asyncio.Event() for r in ROLES}
        self.closed = False


def cleanup(now=None):
    now = time.time() if now is None else now
    for s in [s for s in sessions.values() if s.closed or now - s.created > SESSION_TTL]:
        drop(s)


def drop(s):
    s.closed = True
    sessions.pop(s.id, None)
    for k in s.keys.values():
        by_key.pop(k, None)
    for e in s.ev.values():
        e.set()     # wake any waiting long-poll so it can notice the session is over


def create(game):
    cleanup()
    if len(sessions) >= MAX_SESSIONS:
        return None
    s = Session(game)
    sessions[s.id] = s
    for r, k in s.keys.items():
        by_key[k] = (s, r)
    return s


def lookup(key):
    hit = by_key.get(key or "")
    if not hit:
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
        s.closed = True      # nothing more will be accepted; cleanup() removes it
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


def ice_servers():
    """STUN by default. Set NETPLAY_ICE_JSON to a JSON list (add your TURN relay here)."""
    raw = os.environ.get("NETPLAY_ICE_JSON", "")
    if raw:
        try:
            v = json.loads(raw)
            if isinstance(v, list):
                return v
        except ValueError:
            pass
    return DEFAULT_ICE
