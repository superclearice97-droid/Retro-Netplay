"""Friends for the desktop app, kept simple: your friend code, adding a friend by code (they accept), seeing who's
online and what they're playing, and Join. Joining asks the friend, who taps Allow; then the friend hosts and you
connect to them. Same rules as the Android app (android/core/.../Friends.java, Presence.java) and PROTOCOL.md.
"""
import hashlib
import json
import re
import secrets
import socket
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit

import mmtls

ID_RE = re.compile(r"^[0-9a-f]{24}$")
SHORT_RE = re.compile(r"^[2-9A-HJ-NP-Z]{6}$")      # friend codes like K7M-4QZ (no 0/O/1/I to mix up)
SECRET_RE = re.compile(r"^[0-9a-f]{32,64}$")


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
    if not isinstance(cfg.get("friend_requests"), list):
        cfg["friend_requests"] = []
    cfg["friend_requests"] = [r for r in cfg["friend_requests"]
                              if isinstance(r, dict) and ID_RE.match(str(r.get("id", ""))) and not find(cfg, r["id"])][:50]
    return changed


def norm_friend(f):
    if not isinstance(f, dict) or not ID_RE.match(str(f.get("id", ""))):
        return None
    return {"id": f["id"], "name": clean_name(f.get("name")) or "Friend", "code": pretty_code(f.get("code")),
            "pending": bool(f.get("pending"))}


def short(code):
    """'k7m 4qz' / 'K7M-4QZ' -> 'K7M4QZ', or '' if it isn't a friend code."""
    c = re.sub(r"[\s-]", "", str(code or "")).upper()
    return c if SHORT_RE.match(c) else ""


def pretty_code(code):
    c = short(code)
    return c[:3] + "-" + c[3:] if c else ""


def friend_link(host, tcp_port, code, name):
    return f"http://{host}:{int(tcp_port) + 1}/friend?c={short(code)}&n={quote(clean_name(name))}"


def parse_friend(text):
    """A friend code (K7M-4QZ), a friend link, or an older long code.
    -> {"code": "K7M4QZ", "name"} (needs a lookup) or {"id", "name"}, or None."""
    t = (text or "").strip()
    if short(t):
        return {"code": short(t), "name": "Friend"}
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
    name = clean_name((q.get("n") or ["Friend"])[0]) or "Friend"
    c = short((q.get("c") or [""])[0])
    if c:
        return {"code": c, "name": name}
    fid = (q.get("i") or [""])[0]
    if not ID_RE.match(fid):
        return None
    return {"id": fid, "name": name}


def add_friend(cfg, f):
    """-> 'added', 'known' or 'self'. f: {"id", "name", "code"?, "pending"?}."""
    if f["id"] == id_of(cfg["secret"]):
        return "self"
    cfg["friend_requests"] = [r for r in cfg.get("friend_requests", []) if r.get("id") != f["id"]]
    for x in cfg["friends"]:
        if x["id"] == f["id"]:
            if f.get("name") and f["name"] != "Friend":
                x["name"] = f["name"]
            if f.get("code"):
                x["code"] = pretty_code(f["code"])
            if not f.get("pending"):
                x["pending"] = False
            return "known"
    cfg["friends"].append(norm_friend(f))
    return "added"


def got_request(cfg, d):
    """Someone sent you a friend request. If you'd already asked them, you're simply friends now."""
    f = find(cfg, d["id"])
    if f:
        f["pending"] = False
        return "friends"
    if not any(r["id"] == d["id"] for r in cfg["friend_requests"]):
        cfg["friend_requests"].append({"id": d["id"], "name": clean_name(d.get("name")) or "Player",
                                       "code": pretty_code(d.get("code"))})
    return "new"


def find(cfg, fid):
    return next((f for f in cfg.get("friends", []) if f["id"] == fid), None)


def friend_ids(cfg):
    return [f["id"] for f in cfg.get("friends", [])]


def search_fields(cfg, prefer_host=False):
    """Extra fields for a search: who you are (so a friend's join knows who hosts) and your name."""
    m = {"prefer_host": bool(prefer_host)}
    if SECRET_RE.match(str(cfg.get("secret", ""))):
        m.update(me=cfg["secret"], name=clean_name(cfg.get("my_name", "")))
    return m


class Presence(threading.Thread):
    """The "hello" connection. Calls on(event, data) from its own thread; events: up, down, online, call,
    start, declined, failed, cancelled, lookup, friend_request, friend_accept, request_sent, soft_error."""

    def __init__(self, host, port, secret, name, friend_ids, on):
        super().__init__(daemon=True)
        self.host, self.port, self.secret, self.name = host, int(port), secret, name
        self.friend_ids, self.on = list(friend_ids), on
        self.sock, self.stopped, self.lock = None, False, threading.Lock()
        self.state, self.game, self.can = "idle", "", None

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

    def status(self, state, game="", can=None):
        self.state, self.game, self.can = state, game or "", can
        m = {"op": "status", "state": state, "game": self.game}
        if can is not None:
            m["can"] = can
        self.send(m)

    def set_friends(self, ids):
        """The friend list changed: tell the matchmaker on this same connection (it's re-sent after a reconnect)."""
        self.friend_ids = list(ids)
        self.send({"op": "set_friends", "friends": self.friend_ids})

    def set_name(self, name):
        """A new name only shows after reconnecting, so do that."""
        self.name = name
        s = self.sock
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def call(self, fid, game):
        cid = secrets.token_hex(6)
        return cid if self.send({"op": "call", "to": fid, "game": game, "kind": "play", "call": cid}) else ""

    def answer(self, caller, cid, ok, why=""):
        m = {"op": "answer", "to": caller, "call": cid, "ok": bool(ok)}
        if why:
            m["msg"] = why
        self.send(m)

    def cancel(self, fid, cid):
        self.send({"op": "cancel_call", "to": fid, "call": cid})

    def lookup(self, code):
        return self.send({"op": "lookup", "code": short(code)})

    def request(self, fid):
        return self.send({"op": "friend_request", "to": fid})

    def accept(self, fid):
        return self.send({"op": "friend_accept", "to": fid})

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
            backoff = min(30, backoff * 2)

    def _session(self):
        s = mmtls.connect(self.host, self.port, 8)     # (encrypted when the matchmaker offers it)
        s.settimeout(30)
        self.sock = s
        try:
            self.send({"v": 1, "op": "hello", "me": self.secret, "name": self.name, "friends": self.friend_ids})
            if self.state != "idle":
                self.status(self.state, self.game, self.can)
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
                    self._on_message(m)
        finally:
            self.sock = None
            try:
                s.close()
            except OSError:
                pass

    def _on_message(self, m):
        op = m.get("op")
        if op == "ping":
            self.send({"op": "ping"})
        elif op == "error":
            raise OSError(str(m.get("msg")))
        elif op == "hello":
            self.on("up", {"id": m.get("id"), "code": pretty_code(m.get("code"))})
        elif op == "lookup":
            self.on("lookup", {"code": pretty_code(m.get("code")), "found": bool(m.get("found")) and ID_RE.match(str(m.get("id", ""))) is not None,
                               "id": str(m.get("id", "")), "name": clean_name(m.get("name")) or "Player", "self": bool(m.get("self"))})
        elif op in ("friend_request", "friend_accept") and ID_RE.match(str(m.get("from", ""))):
            self.on(op, {"id": m["from"], "name": clean_name(m.get("name")) or "Player", "code": pretty_code(m.get("code"))})
        elif op == "error_soft":
            self.on("soft_error", str(m.get("msg", "")))
        elif op == "friends":
            self.on("online", [x for x in m.get("online", []) if isinstance(x, dict) and ID_RE.match(str(x.get("id", "")))])
        elif op == "request_sent":
            self.on("request_sent", str(m.get("to", "")))
        elif op == "call" and ID_RE.match(str(m.get("from", ""))):
            self.on("call", {"from": m["from"], "name": clean_name(m.get("name")) or "A friend", "game": str(m.get("game", "")),
                             "call": str(m.get("call", ""))})
        elif op == "start":
            w = m.get("with") if isinstance(m.get("with"), dict) else {}
            self.on("start", {"code": str(m.get("code", "")), "game": str(m.get("game", "")), "as": m.get("as"),
                              "with": clean_name(w.get("name")) or "your friend", "with_id": str(w.get("id", ""))})
        elif op == "declined":
            why = m.get("msg")
            self.on("declined", {"name": clean_name(m.get("name")) or "Your friend",
                                 "why": why[:160] if isinstance(why, str) and why.isprintable() else ""})
        elif op == "call_failed":
            self.on("failed", str(m.get("msg", "")))
        elif op == "call_cancelled":
            self.on("cancelled", str(m.get("call", "")))
