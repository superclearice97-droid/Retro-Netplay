"""The arcade lobby (protocol v2, see PROTOCOL.md "Lobby"). Any player's app can run one.

Like Parsec Arcade: a host posts a ROOM for the game they are playing (title, open seats, a note). Everyone in the
lobby sees the open rooms and can ask to join; the host accepts, or lets people straight in (auto-accept). A seated
player either gets a stream of the host's screen (they need nothing but the app) or, for games whose emulator has
its own netplay (Dolphin, RetroArch), what their emulator needs to connect to the host's.

Also: lobby chat and room chat, blocking, reports, and moderation by whoever runs the lobby (their own app, on the
same computer, which can read the lobby's private admin token, is its moderator). The lobby keeps rooms, chat and mutes in memory only; on disk it keeps
lobby-wide bans (lobby-bans.json) and reports (lobby-reports.jsonl) in its data folder, for the person running it.

One connection per player: the first line is {"op": "lobby", "v": 2, "me": <secret>, "name": ...}, then
newline-delimited JSON both ways.
"""
import asyncio
import hashlib
import json
import os
import re
import secrets
import time

import sessions

GAME_RE = re.compile(r"^[a-z0-9]+:[a-z0-9-]+$")
SYSTEM_RE = re.compile(r"^[a-z0-9]{1,16}$")
ID_RE = re.compile(r"^[0-9a-f]{24}$")
SECRET_RE = re.compile(r"^[0-9a-f]{32,64}$")
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{6,16}$")
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_RE = re.compile(r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}$")
NATIVE_KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,24}$")
MODES = ("netplay", "stream")
REPORT_REASONS = ("abuse", "cheating", "spam", "name", "underage", "other")

MAX_LINE = 8192
MAX_MEMBERS = 5000
MAX_PER_IP = 8
MAX_ROOMS = 2000
MAX_BUFFER = 1 << 20          # a player who stops reading gets disconnected before their backlog grows past this
PING_SECONDS = 20
IDLE_SECONDS = 70             # no message at all (not even a pong) for this long: gone
REQUEST_SECONDS = 60          # an unanswered join request expires
CHAT_MAX = 300
CHAT_BURST, CHAT_WINDOW = 5, 10.0       # at most 5 messages in 10 s
HISTORY = 30                  # recent lobby messages shown to someone who just arrived (memory only)
LIMITS = {"post": (6, 60), "join": (10, 60), "report": (5, 600)}    # (count, seconds) per player

members = {}                  # player id -> Member
rooms = {}                    # room id -> Room
by_invite = {}                # invite code -> room id
history = []                  # recent lobby chat messages
per_ip = {}
bans = {}                     # player id -> until (epoch seconds; 0 = for good)
INFO = {"name": "Retro-Netplay lobby", "rules": "Be kind. No hate, no harassment, no spam. 13+ only."}
_data = None                  # data folder (set by setup)
ADMIN_TOKEN = ""              # in lobby-admin.token (readable only by the account running the lobby)
words = []                    # words the person running the lobby wants masked in chat (lobby-words.txt)


def setup(data_dir, name=""):
    """Load bans, the chat word list and the lobby's name and rules (lobby.json) from the server's data folder."""
    global _data, words, ADMIN_TOKEN
    _data = data_dir
    ADMIN_TOKEN = admin_token(data_dir)
    if name:
        INFO["name"] = name
    try:
        with open(os.path.join(data_dir, "lobby.json")) as f:
            raw = json.load(f)
        for k in ("name", "rules"):
            if isinstance(raw.get(k), str) and raw[k].strip():
                INFO[k] = clean_text(raw[k], 48 if k == "name" else 600)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with open(os.path.join(data_dir, "lobby-bans.json")) as f:
            bans.update({k: float(v) for k, v in json.load(f).items() if ID_RE.match(k)})
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        with open(os.path.join(data_dir, "lobby-words.txt"), encoding="utf-8") as f:
            words = [w.strip().lower() for w in f if w.strip() and not w.startswith("#")][:2000]
    except OSError:
        words = []


def admin_token(data_dir):
    """The moderator's token: kept in a file only this account can read, made the first time."""
    path = os.path.join(data_dir, "lobby-admin.token")
    try:
        with open(path) as f:
            tok = f.read().strip()
        if len(tok) >= 32:
            return tok
    except OSError:
        pass
    tok = secrets.token_hex(24)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(tok)
    except OSError:
        return ""
    return tok


def player_id(secret):
    return hashlib.sha256(secret.encode("ascii")).hexdigest()[:24]


def clean_text(t, n):
    if not isinstance(t, str):
        return ""
    t = "".join(c for c in t if c.isprintable() and c not in "<>")
    return " ".join(t.split())[:n]


def mask(text):
    """Hide the lobby's own banned words (whole words, any case) behind asterisks."""
    if not words:
        return text
    def sub(m):
        return "*" * len(m.group(0)) if m.group(0).lower() in words else m.group(0)
    return re.sub(r"[^\W_]+", sub, text)


def is_loopback(ip):
    return ip == "::1" or ip.startswith("127.") or ip.startswith("::ffff:127.")


def banned(pid, now=None):
    until = bans.get(pid)
    if until is None:
        return False
    if until and until < (now or time.time()):
        bans.pop(pid, None)
        return False
    return True


def save_bans():
    if not _data:
        return
    try:
        path = os.path.join(_data, "lobby-bans.json")
        fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(bans, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


class Member:
    def __init__(self, writer, req, ip):
        self.writer, self.ip = writer, ip
        self.id = player_id(req["me"])
        self.name = clean_text(req.get("name"), 24) or "Player"
        self.client = clean_text(req.get("client"), 24)
        self.blocked = {x for x in (req.get("blocked") or [])[:500] if isinstance(x, str) and ID_RE.match(x)}
        # the lobby's own app (it can read the private admin token file) moderates the lobby
        self.admin = bool(ADMIN_TOKEN) and secrets.compare_digest(str(req.get("admin", "")), ADMIN_TOKEN)
        self.chat_times, self.muted_until = [], 0.0
        self.times = {k: [] for k in LIMITS}
        self.room = None                      # the Room they host
        self.seat = None                      # (Room, seat number) they sit in
        self.waiting = None                   # request id while they wait for a host's answer
        self.recent = []                      # their last messages (for a report's context)
        self.last_heard = time.monotonic()
        self.closed = False

    def view(self):
        return {"id": self.id, "name": self.name}


class Room:
    def __init__(self, host, r):
        self.id = secrets.token_urlsafe(6)
        self.host = host
        self.created = time.time()
        self.invite = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(6))
        self.seated = {}                      # seat number (2..) -> Member
        self.requests = {}                    # request id -> (Member, expiry)
        self.banned = set()                   # players the host removed for good
        self.sessions = {}                    # seat number -> sessions.Session (stream rooms)
        self.apply(r)

    def apply(self, r):
        self.game = r["game"]
        self.title = r["title"]
        self.system = r["system"]
        self.mode = r["mode"]
        self.seats = r["seats"]
        self.note = r["note"]
        self.auto = r["auto"]
        self.public = r["public"]
        self.native = r["native"]

    def taken(self):
        return 1 + len(self.seated)

    def free_seat(self):
        return next((n for n in range(2, self.seats + 1) if n not in self.seated), None)

    def view(self, full=False):
        v = {"id": self.id, "title": self.title, "game": self.game, "system": self.system, "mode": self.mode,
             "seats": self.seats, "taken": self.taken(), "host": self.host.view(), "note": self.note,
             "auto": self.auto, "public": self.public, "created": int(self.created)}
        if full:      # for the people in the room
            v["players"] = [dict(self.host.view(), seat=1)] + [dict(m.view(), seat=n) for n, m in sorted(self.seated.items())]
            v["invite"] = self.invite
        return v

    def people(self):
        return [self.host] + list(self.seated.values())


def send(m, msg):
    if m.closed:
        return
    tr = m.writer.transport
    if tr is None or tr.is_closing():
        return
    if tr.get_write_buffer_size() > MAX_BUFFER:      # not reading: drop them rather than queue without end
        m.closed = True
        tr.abort()
        return
    m.writer.write((json.dumps(msg) + "\n").encode())


def soft(m, about, msg):
    send(m, {"op": "error_soft", "about": about, "msg": msg})


def limited(m, kind, now):
    count, window = LIMITS[kind]
    t = [x for x in m.times[kind] if now - x < window]
    m.times[kind] = t
    if len(t) >= count:
        return True
    t.append(now)
    return False


_rooms_pending = False


def rooms_changed():
    """Tell everyone the list of open rooms changed (once per moment, however many changes happened)."""
    global _rooms_pending
    if _rooms_pending:
        return
    _rooms_pending = True

    def push():
        global _rooms_pending
        _rooms_pending = False
        lst = public_rooms()
        for m in list(members.values()):
            send(m, {"op": "rooms", "rooms": lst, "online": len(members)})
    asyncio.get_running_loop().call_later(0.15, push)


def public_rooms():
    return [r.view() for r in sorted(rooms.values(), key=lambda r: r.created) if r.public]


def room_update(room):
    v = room.view(full=True)
    for m in room.people():
        send(m, {"op": "room", "room": v})
    rooms_changed()


def clean_room(r, old=None):
    """-> (room dict, None) or (None, problem). old: the room being updated (fields not given stay)."""
    if not isinstance(r, dict):
        return None, "bad room"
    base = {"game": old.game, "title": old.title, "system": old.system, "mode": old.mode, "seats": old.seats,
            "note": old.note, "auto": old.auto, "public": old.public, "native": old.native} if old else {}
    out = dict(base)
    for k in ("game", "title", "system", "mode", "seats", "note", "auto", "public", "native"):
        if k in r:
            out[k] = r[k]
    if old and (out["game"] != old.game or out["mode"] != old.mode):
        return None, "a room's game and mode can't change; close it and post a new one"
    if not isinstance(out.get("game"), str) or not GAME_RE.match(out["game"]):
        return None, "bad game"
    out["title"] = clean_text(out.get("title"), 48)
    if not out["title"]:
        return None, "the room needs a title"
    if not isinstance(out.get("system"), str) or not SYSTEM_RE.match(out["system"]):
        return None, "bad system"
    if out.get("mode") not in MODES:
        return None, "bad mode"
    seats = out.get("seats")
    if isinstance(seats, bool) or not isinstance(seats, int) or not 2 <= seats <= 4:
        return None, "a room has 2 to 4 seats"
    if old and seats < old.taken():
        return None, "there are more players in the room than that"
    out["note"] = clean_text(out.get("note", ""), 120)
    out["auto"] = out.get("auto") is True
    out["public"] = out.get("public", True) is not False
    nat = out.get("native") or {}
    if not isinstance(nat, dict):
        return None, "bad native"
    clean = {}
    for k, v in list(nat.items())[:8]:
        if not isinstance(k, str) or not NATIVE_KEY_RE.match(k) or k == "ip":
            continue
        if isinstance(v, bool) or (isinstance(v, int) and abs(v) < 1 << 31):
            clean[k] = v
        elif isinstance(v, str) and len(v) <= 64 and v.isprintable():
            clean[k] = v
    out["native"] = clean
    return out, None


def close_room(room, why):
    if rooms.pop(room.id, None) is None:
        return
    by_invite.pop(room.invite, None)
    room.host.room = None
    for s in room.sessions.values():
        sessions.drop(s)
    for m in room.seated.values():
        m.seat = None
        send(m, {"op": "room_closed", "room": room.id, "why": why})
    for m, _exp in room.requests.values():
        m.waiting = None
        send(m, {"op": "declined", "room": room.id, "why": why})
    room.seated.clear()
    room.requests.clear()
    rooms_changed()


def unseat(room, n, why, tell_guest=True):
    m = room.seated.pop(n, None)
    s = room.sessions.pop(n, None)
    if s:
        sessions.drop(s)
    if m:
        m.seat = None
        if tell_guest:
            send(m, {"op": "removed", "room": room.id, "why": why})
        send(room.host, {"op": "left", "room": room.id, "seat": n, "player": m.view(), "why": why})
        room_update(room)


def seat(room, m):
    """Give m the next free seat: a stream session (key for each side) or the host's emulator details."""
    n = room.free_seat()
    if n is None:
        send(m, {"op": "declined", "room": room.id, "why": "the room is full"})
        return
    room.seated[n] = m
    m.seat = (room, n)
    to_guest = {"op": "accepted", "room": room.view(full=True), "seat": n}
    to_host = {"op": "seated", "room": room.id, "seat": n, "player": m.view()}
    if room.mode == "stream":
        s = sessions.create(room.game)
        if s is None:
            room.seated.pop(n, None)
            m.seat = None
            send(m, {"op": "declined", "room": room.id, "why": "the lobby is too busy right now"})
            return
        room.sessions[n] = s
        to_guest["session"] = {"key": s.keys["guest"], "sig": "tcp"}
        to_host["session"] = {"key": s.keys["host"], "sig": "tcp"}
    else:
        nat = dict(room.native)
        if "port" in nat:        # the emulator connects straight to the host: it needs the host's address
            here = (m.writer.get_extra_info("sockname") or [""])[0]
            nat["ip"] = here if is_loopback(room.host.ip) and here and not is_loopback(here) else room.host.ip
        to_guest["native"] = nat
    send(m, to_guest)
    send(room.host, to_host)
    room_update(room)


def expire_requests(now):
    for room in list(rooms.values()):
        for rid, (m, exp) in list(room.requests.items()):
            if exp < now:
                room.requests.pop(rid, None)
                m.waiting = None
                send(m, {"op": "declined", "room": room.id, "why": "the host didn't answer"})
                send(room.host, {"op": "request_gone", "req": rid})


def find_room(m, msg):
    rid, code = msg.get("room"), str(msg.get("invite", "")).upper()
    if isinstance(rid, str) and ROOM_RE.match(rid):
        return rooms.get(rid)
    if INVITE_RE.match(code):
        return rooms.get(by_invite.get(code, ""))
    return None


def chat(m, msg, now):
    to = msg.get("to")
    text = clean_text(msg.get("text"), CHAT_MAX)
    if not text:
        return
    if m.muted_until > now:
        return soft(m, "chat", f"You're muted for {int(m.muted_until - now) // 60 + 1} more minute(s).")
    m.chat_times = [t for t in m.chat_times if now - t < CHAT_WINDOW]
    if len(m.chat_times) >= CHAT_BURST:
        return soft(m, "chat", "Slow down a little.")
    m.chat_times.append(now)
    out = {"op": "chat", "to": "lobby", "from": m.view(), "text": mask(text), "ts": int(time.time())}
    m.recent = (m.recent + [text])[-10:]
    if to == "lobby":
        targets = list(members.values())
        history.append(out)
        del history[:-HISTORY]
    else:
        room = rooms.get(to) if isinstance(to, str) else None
        if room is None or m not in room.people():
            return soft(m, "chat", "You're not in that room.")
        out["to"] = room.id
        targets = room.people()
    for x in targets:
        if m.id not in x.blocked:
            send(x, out)


def report(m, msg, now):
    pid, why = msg.get("player"), msg.get("why")
    if not isinstance(pid, str) or not ID_RE.match(pid) or why not in REPORT_REASONS:
        return soft(m, "report", "bad report")
    if limited(m, "report", now):
        return soft(m, "report", "Too many reports; wait a few minutes.")
    other = members.get(pid)
    entry = {"t": int(time.time()), "from": m.view(), "player": {"id": pid, "name": other.name if other else ""},
             "why": why, "text": clean_text(msg.get("text"), CHAT_MAX), "their_last_messages": other.recent if other else []}
    if _data:
        try:
            fd = os.open(os.path.join(_data, "lobby-reports.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass
    for x in members.values():
        if x.admin:
            send(x, dict(entry, op="report"))
    send(m, {"op": "reported", "player": pid})


def moderate(m, msg, now):
    if not m.admin:
        return soft(m, "mod", "Only the person running this lobby can do that.")
    act, pid = msg.get("action"), msg.get("player")
    if not isinstance(pid, str) or not ID_RE.match(pid) or act not in ("mute", "kick", "ban", "unban"):
        return soft(m, "mod", "bad request")
    minutes = msg.get("minutes", 0)
    minutes = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) and 0 <= minutes <= 525600 else 0
    other = members.get(pid)
    if act == "unban":
        bans.pop(pid, None)
        save_bans()
    elif act == "mute" and other:
        other.muted_until = now + 60 * (minutes or 10)
        send(other, {"op": "muted", "minutes": minutes or 10})
    elif act in ("kick", "ban"):
        if act == "ban":
            bans[pid] = time.time() + 60 * minutes if minutes else 0
            save_bans()
        if other and not other.admin:
            send(other, {"op": "bye", "why": "You were removed from this lobby." if act == "kick" else
                         "You were banned from this lobby."})
            other.closed = True
            other.writer.close()
    send(m, {"op": "mod_done", "action": act, "player": pid})


def on_message(m, msg, now):
    op = msg.get("op")
    if op == "pong":
        return
    if op == "chat":
        return chat(m, msg, now)
    if op == "block":
        m.blocked = {x for x in (msg.get("ids") or [])[:500] if isinstance(x, str) and ID_RE.match(x)}
        return
    if op == "report":
        return report(m, msg, now)
    if op == "mod":
        return moderate(m, msg, now)
    if op == "post":
        if m.room:
            return soft(m, "post", "You already have a room open.")
        if m.seat:
            return soft(m, "post", "Leave the room you're in first.")
        if limited(m, "post", now):
            return soft(m, "post", "Wait a minute before posting again.")
        if len(rooms) >= MAX_ROOMS:
            return soft(m, "post", "This lobby is full right now.")
        r, problem = clean_room(msg.get("room"))
        if problem:
            return soft(m, "post", problem)
        room = Room(m, r)
        rooms[room.id] = room
        by_invite[room.invite] = room.id
        m.room = room
        send(m, {"op": "posted", "room": room.view(full=True)})
        return rooms_changed()
    if op == "update":
        if not m.room:
            return soft(m, "update", "You have no room open.")
        r, problem = clean_room(msg.get("room"), m.room)
        if problem:
            return soft(m, "update", problem)
        changed = r["native"] != m.room.native
        m.room.apply(r)
        if changed and m.room.mode == "netplay" and "port" not in r["native"]:
            for guest in m.room.seated.values():        # e.g. Dolphin's host code, for the players already in
                send(guest, {"op": "native", "room": m.room.id, "native": dict(r["native"])})
        return room_update(m.room)
    if op == "close":
        if m.room:
            close_room(m.room, "The host closed the room.")
        return
    if op == "join":
        room = find_room(m, msg)
        if room is None:
            return soft(m, "join", "That room is gone.")
        if room.host is m or m.seat or m.room or m.waiting:
            return soft(m, "join", "You're already in a room.")
        if limited(m, "join", now):
            return soft(m, "join", "Too many tries; wait a minute.")
        if m.id in room.banned or m.id in room.host.blocked:
            return send(m, {"op": "declined", "room": room.id, "why": "The host isn't taking requests from you."})
        if room.free_seat() is None:
            return send(m, {"op": "declined", "room": room.id, "why": "the room is full"})
        if room.auto:
            return seat(room, m)
        rid = secrets.token_urlsafe(6)
        room.requests[rid] = (m, now + REQUEST_SECONDS)
        m.waiting = rid
        send(room.host, {"op": "request", "req": rid, "room": room.id, "player": m.view()})
        return send(m, {"op": "waiting", "room": room.id, "req": rid})
    if op == "cancel_join":
        for room in rooms.values():
            if m.waiting in room.requests:
                room.requests.pop(m.waiting)
                send(room.host, {"op": "request_gone", "req": m.waiting})
        m.waiting = None
        return
    if op == "answer":
        room, rid = m.room, msg.get("req")
        if not room or rid not in room.requests:
            return soft(m, "answer", "That request is gone.")
        guest, _exp = room.requests.pop(rid)
        guest.waiting = None
        if msg.get("accept") is True:
            return seat(room, guest)
        return send(guest, {"op": "declined", "room": room.id, "why": "The host said no this time."})
    if op == "leave":
        if m.seat:
            room, n = m.seat
            unseat(room, n, "left", tell_guest=False)
        return
    if op == "kick":
        room = m.room
        pid = msg.get("player")
        if not room:
            return
        n = next((n for n, x in room.seated.items() if x.id == pid), None)
        if msg.get("ban") is True and isinstance(pid, str) and ID_RE.match(pid):
            room.banned.add(pid)
        if n:
            unseat(room, n, "The host removed you from the room.")
        return


async def pinger(m):
    while not m.closed:
        await asyncio.sleep(PING_SECONDS)
        if time.monotonic() - m.last_heard > IDLE_SECONDS:
            m.writer.close()
            return
        send(m, {"op": "ping"})
        expire_requests(time.monotonic())


def validate_hello(req, ip):
    if not isinstance(req.get("me"), str) or not SECRET_RE.match(req["me"]):
        return "bad player secret"
    if req.get("v") != 2:
        return "This lobby needs a newer Retro-Netplay. Update the app."
    if len(members) >= MAX_MEMBERS:
        return "This lobby is full right now. Try again later."
    if per_ip.get(ip, 0) >= MAX_PER_IP:
        return "Too many connections from your network."
    if banned(player_id(req["me"])):
        return "You were banned from this lobby."
    return None


async def member_conn(reader, writer, req, ip):
    """A player's lobby connection, from its first line until it closes."""
    problem = validate_hello(req, ip)
    if problem:
        writer.write((json.dumps({"op": "error", "msg": problem}) + "\n").encode())
        await writer.drain()
        return
    m = Member(writer, req, ip)
    old = members.get(m.id)
    if old:                 # the same player again (a second window, or a reconnect): the new one wins
        old.closed = True
        old.writer.close()
        await asyncio.sleep(0)
        leave(old)
    members[m.id] = m
    per_ip[ip] = per_ip.get(ip, 0) + 1
    send(m, {"op": "welcome", "v": 2, "id": m.id, "name": m.name, "admin": m.admin, "lobby": dict(INFO),
             "rooms": public_rooms(), "online": len(members), "chat": [h for h in history if h["from"]["id"] not in m.blocked]})
    rooms_changed()
    ping = asyncio.create_task(pinger(m))
    try:
        while not m.closed:
            line = await reader.readline()
            if not line:
                break
            if len(line) > MAX_LINE:
                break
            m.last_heard = time.monotonic()
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                on_message(m, msg, time.monotonic())
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, OSError):
        pass
    finally:
        ping.cancel()
        if members.get(m.id) is m:
            leave(m)
        per_ip[ip] = per_ip.get(ip, 1) - 1
        if per_ip[ip] <= 0:
            per_ip.pop(ip, None)
        m.closed = True
        writer.close()


def leave(m):
    """A player left the lobby: close their room, free their seat, drop their pending request."""
    if members.get(m.id) is m:
        members.pop(m.id)
    if m.room:
        close_room(m.room, "The host left.")
    if m.seat:
        room, n = m.seat
        unseat(room, n, "left", tell_guest=False)
    if m.waiting:
        for room in rooms.values():
            if room.requests.pop(m.waiting, None):
                send(room.host, {"op": "request_gone", "req": m.waiting})
        m.waiting = None
    rooms_changed()
