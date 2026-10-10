"""Friends for the desktop app: your id, friend links, privileges, the friends-online connection and calls.
Same rules as the Android app (android/core/.../Friends.java, Presence.java) and PROTOCOL.md, Friends.

Privileges stay in your own settings file. Only "priority" is sent to the matchmaker, because it does the matching.
"""
import hashlib
import json
import re
import secrets
import socket
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit

ID_RE = re.compile(r"^[0-9a-f]{24}$")
SECRET_RE = re.compile(r"^[0-9a-f]{32,64}$")
PRIVILEGES = (
    ("priority", "Priority: matched with me before anyone else"),
    ("join", "Join anytime: can drop into my game without asking"),
    ("watch", "Watch: can watch my games without asking"),
    ("slot", "Pick a controller slot (Player 2-4) on streams I host"),
)


def new_secret():
    return secrets.token_hex(16)


def id_of(secret):
    return hashlib.sha256(secret.encode("ascii")).hexdigest()[:24]


def pretty(fid):
    return "-".join(fid[i:i + 4] for i in range(0, len(fid), 4))


def clean_name(n):
    if not isinstance(n, str):
        return ""
    n = "".join(c for c in n if c.isprintable() and c not in '<>"\\')
    return " ".join(n.split())[:24].strip()


def ensure_identity(cfg):
    """Give this install a secret and a default name the first time. Returns True if cfg changed."""
    changed = False
    if not SECRET_RE.match(str(cfg.get("secret", ""))):
        cfg["secret"], changed = new_secret(), True
    if not clean_name(cfg.get("my_name", "")):
        cfg["my_name"], changed = "Player " + id_of(cfg["secret"])[:4].upper(), True
    if not isinstance(cfg.get("friends"), list):
        cfg["friends"], changed = [], True
    cfg["friends"] = [f for f in (norm_friend(x) for x in cfg["friends"]) if f]
    return changed


def norm_friend(f):
    if not isinstance(f, dict) or not ID_RE.match(str(f.get("id", ""))):
        return None
    out = {"id": f["id"], "name": clean_name(f.get("name")) or "Friend"}
    for k, _ in PRIVILEGES:
        out[k] = bool(f.get(k))
    return out


def friend_link(host, tcp_port, fid, name):
    return f"http://{host}:{int(tcp_port) + 1}/friend?i={fid}&n={quote(clean_name(name))}"


def parse_friend(text):
    """A friend link (web or netplay://) or a typed code. -> {"id","name"} or None."""
    t = (text or "").strip()
    bare = re.sub(r"[\s-]", "", t.lower())
    if ID_RE.match(bare):
        return {"id": bare, "name": "Friend"}
    try:
        u = urlsplit(t)
    except ValueError:
        return None
    app = u.scheme == "netplay" and u.netloc == "friend"
    web = u.scheme in ("http", "https") and u.path == "/friend"
    if not (app or web):
        return None
    q = parse_qs(u.query)
    fid = (q.get("i") or [""])[0]
    if not ID_RE.match(fid):
        return None
    return {"id": fid, "name": clean_name((q.get("n") or ["Friend"])[0]) or "Friend"}


def add_friend(cfg, f):
    """-> 'added', 'known' or 'self'."""
    if f["id"] == id_of(cfg["secret"]):
        return "self"
    for x in cfg["friends"]:
        if x["id"] == f["id"]:
            if f.get("name") and f["name"] != "Friend":
                x["name"] = f["name"]
            return "known"
    cfg["friends"].append(norm_friend(f))
    return "added"


def find(cfg, fid):
    return next((f for f in cfg.get("friends", []) if f["id"] == fid), None)


def search_fields(cfg, prefer_host=False, friends_only=None):
    """Extra fields for a search request."""
    m = {"prefer_host": bool(prefer_host)}
    if SECRET_RE.match(str(cfg.get("secret", ""))):
        fl = cfg.get("friends", [])
        m.update(me=cfg["secret"], name=clean_name(cfg.get("my_name", "")),
                 friends=[f["id"] for f in fl], priority=[f["id"] for f in fl if f.get("priority")],
                 friend_wait=15, friends_only=bool(cfg.get("friends_only") if friends_only is None else friends_only))
    return m


def auto_accept(cfg, from_id, kind):
    f = find(cfg, from_id)
    return bool(f and ((kind == "play" and f.get("join")) or (kind == "watch" and f.get("watch"))))


class Presence(threading.Thread):
    """The "hello" connection. Calls on(event, data) from its own thread; events: up, down, online, call,
    start, declined, failed, cancelled."""

    def __init__(self, host, port, secret, name, friend_ids, on):
        super().__init__(daemon=True)
        self.host, self.port, self.secret, self.name = host, int(port), secret, name
        self.friend_ids, self.on = list(friend_ids), on
        self.sock, self.stopped, self.lock = None, False, threading.Lock()
        self.state, self.game = "idle", ""

    def stop(self):
        self.stopped = True
        s = self.sock
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
                s.close()
            except OSError:
                pass

    def send(self, m):
        s = self.sock
        if not s:
            return False
        try:
            with self.lock:
                s.sendall((json.dumps(m) + "\n").encode())
            return True
        except OSError:
            return False

    def status(self, state, game=""):
        self.state, self.game = state, game or ""
        self.send({"op": "status", "state": state, "game": self.game})

    def call(self, fid, game, kind):
        cid = secrets.token_hex(6)
        return cid if self.send({"op": "call", "to": fid, "game": game, "kind": kind, "call": cid}) else ""

    def answer(self, caller, cid, ok):
        self.send({"op": "answer", "to": caller, "call": cid, "ok": bool(ok)})

    def run(self):
        backoff = 2
        while not self.stopped:
            try:
                self._session()
                backoff = 2
            except (OSError, ValueError) as e:
                if not self.stopped:
                    self.on("down", str(e) or "connection lost")
            if self.stopped:
                break
            time.sleep(backoff)
            backoff = min(60, backoff * 2)

    def _session(self):
        s = socket.create_connection((self.host, self.port), 8)
        s.settimeout(30)
        self.sock = s
        try:
            self.send({"v": 1, "op": "hello", "me": self.secret, "name": self.name, "friends": self.friend_ids})
            if self.state != "idle":
                self.status(self.state, self.game)
            buf = b""
            while not self.stopped:
                try:
                    chunk = s.recv(8192)
                except socket.timeout:
                    self.send({"op": "ping"})
                    continue
                if not chunk:
                    raise OSError("the matchmaker hung up")
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    self._handle(m)
        finally:
            self.sock = None
            try:
                s.close()
            except OSError:
                pass

    def _handle(self, m):
        op = m.get("op")
        if op == "ping":
            self.send({"op": "ping"})
        elif op == "error":
            raise OSError(str(m.get("msg")))
        elif op == "hello":
            self.on("up", m.get("id"))
        elif op == "friends":
            self.on("online", [x for x in m.get("online", []) if isinstance(x, dict) and ID_RE.match(str(x.get("id", "")))])
        elif op == "call" and ID_RE.match(str(m.get("from", ""))):
            self.on("call", {"from": m["from"], "name": clean_name(m.get("name")) or "A friend", "game": str(m.get("game", "")),
                             "kind": m.get("kind"), "call": str(m.get("call", ""))})
        elif op == "start":
            w = m.get("with") if isinstance(m.get("with"), dict) else {}
            self.on("start", {"code": str(m.get("code", "")), "game": str(m.get("game", "")), "kind": m.get("kind"),
                              "as": m.get("as"), "with": clean_name(w.get("name")) or "your friend", "with_id": str(w.get("id", ""))})
        elif op == "declined":
            self.on("declined", clean_name(m.get("name")) or "Your friend")
        elif op == "call_failed":
            self.on("failed", str(m.get("msg", "")))
        elif op == "call_cancelled":
            self.on("cancelled", str(m.get("call", "")))
