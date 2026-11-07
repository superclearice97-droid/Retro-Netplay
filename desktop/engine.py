"""Retro-Netplay's engine: everything except the window.

It runs this computer's own lobby (when that's on), stays connected to the lobby the player picked, keeps the game
library, and starts emulators, streams and the player window. The interface (ui/, shown by the desktop shell or a
browser) talks to it over a small local API: http://127.0.0.1:<port>/ with a random token, reachable only from
this computer.

  GET  /               the interface
  GET  /api/state      everything the interface shows, as JSON
  GET  /api/events     the same, pushed again whenever it changes (Server-Sent Events), plus one-off notices
  POST /api/do         {"action": ..., ...}: what the player did (see Engine.do)
  GET  /art?p=<path>   a picture from the library (only files the library found)

  python3 engine.py [--port N] [--no-browser]
"""
import asyncio
import json
import mimetypes
import os
import queue
import re
import secrets
import subprocess
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import app as core            # (the old window's module: settings, emulators, launching; its window is not used)
import gameinfo
import lan
import library
import mmtls
import runtime
import social

UI = runtime.BASE / "ui"
LOBBY_PORT = 7777
CHAT_KEEP = 150
GAME_KEY_RE = re.compile(r"^[a-z0-9]+:[a-z0-9-]+$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
NEW_DEFAULTS = {"age_ok": False, "lobby_port": LOBBY_PORT, "lobbies": [], "lobby": "", "run_lobby": True, "blocked": [], "chat_filter": True,
                "prefer_netplay": True, "seats": 2, "auto_accept": False}


def admin_token():
    """This computer's lobby moderator token (the built-in lobby writes it; only this account can read it)."""
    try:
        import server
        return (Path(server.data_dir()) / "lobby-admin.token").read_text().strip()
    except OSError:
        return ""


def parse_lobby(text):
    """'host', 'host:port' or a netplay://lobby?h=..&p=.. link -> (host, port) or None."""
    text = str(text or "").strip()
    if text.startswith("netplay://"):
        q = parse_qs(urlsplit(text).query)
        text = (q.get("h") or [""])[0] + ":" + (q.get("p") or [str(LOBBY_PORT)])[0]
    m = re.fullmatch(r"(127\.0\.0\.1|localhost)(?::(\d{2,5}))?", text)
    if m:                                         # this computer's own lobby
        return "127.0.0.1", int(m.group(2) or LOBBY_PORT)
    got = lan.parse_address(text)
    if not got:
        return None
    host, port = got
    return host, int(port or LOBBY_PORT)


class LobbyClient(threading.Thread):
    """One connection to a lobby: reads its messages on this thread, reconnects when it drops."""

    def __init__(self, engine, host, port):
        super().__init__(daemon=True)
        self.engine, self.host, self.port = engine, host, port
        self.sock, self.lock, self.stop = None, threading.Lock(), threading.Event()

    def send(self, msg):
        with self.lock:
            if not self.sock:
                return False
            try:
                self.sock.sendall((json.dumps(msg) + "\n").encode())
                return True
            except OSError:
                return False

    def close(self):
        self.stop.set()
        with self.lock:
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass

    def run(self):
        wait = 1
        while not self.stop.is_set():
            try:
                s = mmtls.connect(self.host, self.port, timeout=8)
                s.settimeout(None)
                cfg = self.engine.cfg
                hello = {"op": "lobby", "v": 2, "me": cfg["secret"], "name": cfg.get("my_name") or "Player",
                         "client": "desktop", "blocked": [b["id"] for b in cfg.get("blocked", [])]}
                if lan.is_loopback(self.host) and (tok := admin_token()):
                    hello["admin"] = tok
                s.sendall((json.dumps(hello) + "\n").encode())
                with self.lock:
                    self.sock = s
                f = s.makefile("rb")
                wait = 1
                for line in f:
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        if msg.get("op") == "ping":
                            self.send({"op": "pong"})
                        else:
                            self.engine.on_lobby(msg)
            except (OSError, ValueError) as e:
                if not self.stop.is_set():
                    self.engine.lobby_down(str(e))
            with self.lock:
                self.sock = None
            if self.stop.is_set():
                return
            self.engine.lobby_down("")
            self.stop.wait(wait)
            wait = min(wait * 2, 30)


class Engine:
    def __init__(self):
        self.cfg = core.load_cfg()
        for k, v in NEW_DEFAULTS.items():
            self.cfg.setdefault(k, v)
        social.ensure_identity(self.cfg)
        core.autodetect(self.cfg)
        core.save_cfg(self.cfg)
        self.lock = threading.RLock()
        self.version = 0
        self.listeners = []            # queues of SSE clients
        self.lobby = None              # LobbyClient
        self.lobby_state = {"connected": False, "address": "", "name": "", "rules": "", "admin": False, "online": 0,
                            "error": "", "id": ""}
        self.rooms, self.myroom, self.requests, self.seat = [], None, [], None
        self.chat = {"lobby": [], "room": []}
        self.art = set()               # picture files the interface may load
        self.art_cache = {}
        self.procs = {}                # name -> Popen (emulator, stream hosts per seat, player)
        self.play_note = ""
        self.finder = None
        self.share = {"lan": "", "public": "", "router": "", "port": self.cfg.get("lobby_port", LOBBY_PORT)}    # how others reach this computer's lobby

    # ------------------------------------------------------------------ state for the interface
    def snapshot(self):
        with self.lock:
            cfg = self.cfg
            return {
                "v": self.version,
                "me": {"name": cfg.get("my_name", ""), "id": social.id_of(cfg["secret"])},
                "setup": {"done": bool(cfg.get("setup_done")) and bool(cfg.get("age_ok")), "age_ok": bool(cfg.get("age_ok"))},
                "lobby": dict(self.lobby_state, run_own=bool(cfg.get("run_lobby")), saved=cfg.get("lobbies", []),
                              share=self.share if cfg.get("run_lobby") else None),
                "rooms": self.rooms,
                "myroom": self.myroom,
                "requests": self.requests,
                "seat": self.seat,
                "chat": self.chat,
                "library": self.library(),
                "emulators": {k: {"label": v[0], "path": cfg["paths"].get(k, ""), "ok": core.emu_ok(cfg["paths"].get(k, ""))}
                              for k, v in core.EMUS.items()},
                "systems": {k: {"label": v["label"], "netplay": v["native"]} for k, v in core.SYSTEMS.items()},
                "settings": {k: cfg.get(k) for k in ("chat_filter", "prefer_netplay", "seats", "auto_accept", "sounds",
                                                    "game_folders", "use_relay")},
                "blocked": cfg.get("blocked", []),
                "playing": {"note": self.play_note, "running": sorted(n for n, p in self.procs.items() if p.poll() is None)},
            }

    def library(self):
        out = []
        for i, g in enumerate(self.cfg.get("games", [])):
            info = self.art_cache.get(g["rom"]) or {}
            out.append({"i": i, "name": info.get("name") or g["name"], "system": g["system"], "key": core.game_key(g),
                        "problem": core.boot_problem(self.cfg, g), "screens": list(info.get("screens") or []),
                        **{k: info.get(k, "") for k in ("box", "title", "logo", "fanart", "year", "desc", "developer",
                                                         "publisher", "genre", "players", "rating")}})
        return out

    def changed(self):
        with self.lock:
            self.version += 1
            snap = self.snapshot()
        self._push("state", snap)

    def notice(self, kind, text, **extra):
        """A one-off message for the interface (a toast, a sound)."""
        self._push("notice", dict(extra, kind=kind, text=text))

    def _push(self, event, data):
        for q in list(self.listeners):
            try:
                q.put_nowait((event, data))
            except queue.Full:
                pass

    # ------------------------------------------------------------------ the lobby
    def start(self):
        if self.cfg.get("run_lobby"):
            self.run_own_lobby()
        # pick up games added to the folders since last time (then their art)
        threading.Thread(target=self.scan_folders if self.cfg.get("game_folders") else self.scan_art, daemon=True).start()
        self.connect(self.cfg.get("lobby") or f"127.0.0.1:{self.cfg.get('lobby_port', LOBBY_PORT)}")

    def run_own_lobby(self):
        core.start_local_server(int(self.cfg.get("lobby_port", LOBBY_PORT)), lambda t: self.notice("info", t))
        threading.Thread(target=self.share_lobby, daemon=True).start()

    def share_lobby(self):
        """Find the addresses others use for this computer's lobby, and ask the router to let them in."""
        self.share["lan"] = lan.lan_ip() or ""
        self.changed()
        if not self.cfg.get("auto_ports", True):
            self.share["router"] = "Automatic router setup is off."
        else:
            res = core.PORTS.open(int(self.share["port"]), "TCP")
            self.share["router"] = res["message"]
            gw = getattr(core.PORTS, "gw", None)
            if res["ok"] and gw:
                try:
                    self.share["public"] = gw.external_ip()
                except Exception:
                    pass
        self.changed()

    def connect(self, address):
        got = parse_lobby(address)
        if not got:
            self.notice("error", "That lobby address doesn't look right. It looks like 203.0.113.5:7777.")
            return
        if self.lobby:
            self.lobby.close()
        host, port = got
        with self.lock:
            self.lobby_state.update(connected=False, address=f"{host}:{port}", error="", name="")
            self.rooms, self.myroom, self.requests, self.seat = [], None, [], None
            self.chat = {"lobby": [], "room": []}
            self.cfg["lobby"] = f"{host}:{port}"
            core.save_cfg(self.cfg)
        self.lobby = LobbyClient(self, host, port)
        self.lobby.start()
        self.changed()

    def lobby_down(self, why):
        with self.lock:
            was = self.lobby_state["connected"]
            self.lobby_state.update(connected=False, error=why or self.lobby_state["error"])
            self.rooms, self.myroom, self.requests = [], None, []
        if was:
            self.notice("warn", "Lost the connection to the lobby. Trying again...")
        self.changed()

    def send(self, msg):
        if not (self.lobby and self.lobby.send(msg)):
            self.notice("error", "You're not connected to a lobby right now.")
            return False
        return True

    def on_lobby(self, m):
        op = m.get("op")
        with self.lock:
            if op == "welcome":
                self.lobby_state.update(connected=True, error="", name=m.get("lobby", {}).get("name", ""),
                                        rules=m.get("lobby", {}).get("rules", ""), admin=bool(m.get("admin")),
                                        online=m.get("online", 0), id=m.get("id", ""))
                self.rooms = m.get("rooms", [])
                self.chat["lobby"] = list(m.get("chat", []))[-CHAT_KEEP:]
            elif op == "rooms":
                self.rooms, self.lobby_state["online"] = m.get("rooms", []), m.get("online", 0)
            elif op == "posted":
                self.myroom, self.requests, self.chat["room"] = m.get("room"), [], []
                self.start_host_game()
            elif op == "room":
                r = m.get("room") or {}
                if self.myroom and r.get("id") == self.myroom.get("id"):
                    self.myroom = r
                elif self.seat and r.get("id") == self.seat["room"].get("id"):
                    self.seat["room"] = r
            elif op == "request":
                self.requests.append({"req": m.get("req"), "player": m.get("player", {})})
                self.notice("request", f"{m.get('player', {}).get('name', 'Someone')} wants to join", sound="found")
            elif op == "request_gone":
                self.requests = [r for r in self.requests if r["req"] != m.get("req")]
            elif op == "seated":
                self.requests = [r for r in self.requests if r["player"].get("id") != m.get("player", {}).get("id")]
                self.notice("info", f"{m.get('player', {}).get('name', 'A player')} joined as Player {m.get('seat')}")
                if m.get("session"):
                    self.start_stream_host(m["seat"], m["session"].get("key", ""))
            elif op == "left":
                self.stop_proc(f"stream{m.get('seat')}")
                self.notice("info", f"{m.get('player', {}).get('name', 'A player')} left the room")
            elif op == "waiting":
                self.seat = {"room": next((r for r in self.rooms if r["id"] == m.get("room")), {"id": m.get("room")}),
                             "seat": None, "waiting": True}
            elif op == "accepted":
                self.seat = {"room": m.get("room", {}), "seat": m.get("seat"), "waiting": False}
                self.chat["room"] = []
                self.notice("joined", f"You're in! You're Player {m.get('seat')}.", sound="found")
                self.start_guest(m)
            elif op in ("declined", "removed", "room_closed"):
                self.seat = None
                self.chat["room"] = []
                self.stop_proc("player")
                if op != "declined" and not self.myroom:
                    self.stop_proc("emulator")
                self.notice("warn", m.get("why", "") or "The room closed.")
            elif op == "chat":
                where = "lobby" if m.get("to") == "lobby" else "room"
                self.chat[where] = (self.chat[where] + [m])[-CHAT_KEEP:]
            elif op in ("error_soft", "error"):
                self.notice("error", m.get("msg", "Something went wrong."))
                if op == "error":
                    self.lobby_state["error"] = m.get("msg", "")
            elif op == "report" and self.lobby_state["admin"]:
                self.notice("report", f"Report: {m.get('from', {}).get('name')} reported {m.get('player', {}).get('name')} "
                                      f"({m.get('why')}). Saved in the lobby's reports file.")
            elif op == "reported":
                self.notice("info", "Thanks. The person running this lobby will look at it.")
            elif op in ("muted", "bye"):
                self.notice("warn", m.get("why") or f"You're muted for {m.get('minutes')} minutes.")
            elif op == "mod_done":
                self.notice("info", "Done.")
            else:
                return
        self.changed()

    # ------------------------------------------------------------------ games: hosting
    def game(self, i):
        gs = self.cfg.get("games", [])
        return gs[i] if isinstance(i, int) and 0 <= i < len(gs) else None

    def room_mode(self, g, stream):
        """Netplay when the game's emulator has it (and the host didn't ask for a stream), else a stream."""
        return "stream" if stream or not core.SYSTEMS[g["system"]]["native"] else "netplay"

    def native_details(self, g):
        s = core.SYSTEMS[g["system"]]
        if s["emu"] == "retroarch":
            return {"emu": "retroarch", "port": 55435, "core": s["core"]}
        if s["emu"] == "dolphin":
            return {"emu": "dolphin"}           # the host adds the host code Dolphin shows (update_room)
        return {}

    def post_room(self, a):
        g = self.game(a.get("game"))
        if not g:
            return self.notice("error", "Pick a game first.")
        problem = core.boot_problem(self.cfg, g)
        if problem:
            return self.notice("error", problem)
        mode = self.room_mode(g, a.get("stream"))
        room = {"game": core.game_key(g), "title": str(a.get("title") or g["name"])[:48], "system": g["system"],
                "mode": mode, "seats": int(a.get("seats") or 2), "note": str(a.get("note") or "")[:120],
                "auto": bool(a.get("auto")), "public": a.get("public", True) is not False,
                "native": self.native_details(g) if mode == "netplay" else {}}
        self._hosting = g
        if mode == "netplay" and room["native"].get("port"):       # RetroArch: guests connect straight to this computer
            threading.Thread(target=core.PORTS.open, args=(room["native"]["port"], "TCP"), daemon=True).start()
        self.cfg["seats"], self.cfg["auto_accept"] = room["seats"], room["auto"]
        core.save_cfg(self.cfg)
        self.send({"op": "post", "room": room})

    def start_host_game(self):
        """The room is up: start the game, so the host plays while players come in (like Parsec Arcade)."""
        g = getattr(self, "_hosting", None)
        if not g or (self.procs.get("emulator") and self.procs["emulator"].poll() is None):
            return
        room = self.myroom or {}
        if room.get("mode") == "netplay" and core.SYSTEMS[g["system"]]["emu"] == "retroarch":
            kind, argv = core.build_cmd(self.cfg, g, "native", "host", {})
            note = "RetroArch is hosting netplay. Players who join connect to it by themselves."
        elif room.get("mode") == "netplay":
            argv = core.emu_argv(self.cfg["paths"]["dolphin"])
            note = ("In Dolphin: Tools > NetPlay > Host, choose the game, then paste Dolphin's host code into the room "
                    "so players can connect.")
        else:
            argv = core.solo_cmd(self.cfg, g)
            note = "Playing. Players who join see your game window and control Players 2 to 4."
        self.launch("emulator", argv)
        self.play_note = note

    def start_stream_host(self, seat, key):
        if not KEY_RE.fullmatch(key):
            return
        host, port = parse_lobby(self.cfg.get("lobby")) or ("127.0.0.1", LOBBY_PORT)
        cmd = [runtime.host_python(), str(runtime.host_script()), "--mm", f"{host}:{port}", f"--key={key}",
               "--slot", str(int(seat))] + ([] if self.cfg.get("use_relay", True) else ["--no-relay"])
        self.launch(f"stream{seat}", cmd, log=f"stream-host-{seat}.log")
        self.notice("share", "When the 'share your screen' box opens (only the first time), choose the game window.")

    def close_room(self):
        self.send({"op": "close"})
        with self.lock:
            self.myroom, self.requests = None, []
            for n in list(self.procs):
                if n.startswith("stream"):
                    self.stop_proc(n)
            self.play_note = ""
        self.changed()

    # ------------------------------------------------------------------ games: joining
    def start_guest(self, m):
        room = m.get("room", {})
        if m.get("session"):
            key = m["session"].get("key", "")
            if not KEY_RE.fullmatch(key):
                return self.notice("error", "The lobby sent a stream key that doesn't look right. Try joining again.")
            if "cmd" not in core._guest_cache:
                core.guest_ready()
            cmd = list(core._guest_cache.get("cmd") or [])
            if not cmd:
                return self.notice("error", "The player window is missing from this copy of Retro-Netplay. Download it again.")
            host, port = parse_lobby(self.cfg.get("lobby")) or ("127.0.0.1", LOBBY_PORT)
            name = social.clean_name(room.get("host", {}).get("name"))
            cmd += ["--mm", f"{host}:{port}", f"--key={key}"] + ([f"--name={name}"] if name else []) + (
                [] if self.cfg.get("use_relay", True) else ["--no-relay"])
            self.launch("player", cmd, log="stream-guest.log")
            self.play_note = (f"{room.get('host', {}).get('name', 'The host')}'s game opens in its own window. "
                              "F11: full screen. Esc twice: leave.")
            return
        nat = m.get("native") or {}
        g = next((x for x in self.cfg.get("games", []) if core.game_key(x) == room.get("game")), None)
        if not g:
            self.play_note = (f"This room uses netplay: you need your own copy of {room.get('title', 'the game')}. "
                              "Add it to your library, then join again.")
            return self.notice("warn", self.play_note)
        if nat.get("emu") == "retroarch" and nat.get("ip"):
            s = core.SYSTEMS[g["system"]]
            argv = core.emu_argv(self.cfg["paths"]["retroarch"]) + ["-L", core.find_core(self.cfg, s["core"]), g["rom"],
                                                                    "--connect", str(nat["ip"]), "--port", str(int(nat.get("port", 55435)))]
            self.launch("emulator", argv)
            self.play_note = "RetroArch is connecting to the host's game."
        elif nat.get("emu") == "dolphin":
            self.launch("emulator", core.emu_argv(self.cfg["paths"]["dolphin"]))
            code = nat.get("code", "")
            self.play_note = (f"In Dolphin: Tools > NetPlay > Connect, choose Traversal Server and enter the host code {code}."
                              if code else "Wait for the host to share Dolphin's host code in the room, then connect in Dolphin.")

    # ------------------------------------------------------------------ programs
    def launch(self, name, argv, log=None):
        self.stop_proc(name)
        out = subprocess.DEVNULL
        if log:
            try:
                p = core.CONFIG_PATH.parent / log
                p.parent.mkdir(parents=True, exist_ok=True)
                out = open(p, "w")
            except OSError:
                out = subprocess.DEVNULL
        try:
            self.procs[name] = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT)
        except OSError as e:
            self.notice("error", f"Couldn't start {argv[0] if argv else name}: {e}")

    def stop_proc(self, name):
        p = self.procs.pop(name, None)
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    # ------------------------------------------------------------------ library
    def scan_art(self):
        if self.finder is None:
            ra = self.cfg["paths"].get("retroarch", "")
            self.finder = gameinfo.Finder([str(Path(ra).parent)] if ra else [])
        for g in list(self.cfg.get("games", [])):
            try:
                info = self.finder.find(g)
            except Exception:
                continue
            with self.lock:
                self.art_cache[g["rom"]] = info
                for p in [info.get(k) for k in ("box", "title", "logo", "fanart")] + list(info.get("screens") or []):
                    if p:
                        self.art.add(str(p))
        self.changed()

    def scan_folders(self):
        games, _questions = library.scan(self.cfg.get("game_folders", []), self.cfg.get("folder_answers", {}))
        with self.lock:
            self.cfg["games"] = library.merge(self.cfg.get("games", []), games)
            core.save_cfg(self.cfg)
        self.changed()
        self.scan_art()

    # ------------------------------------------------------------------ what the player did
    def do(self, a):
        act = a.get("action")
        cfg = self.cfg
        if act == "setup":
            name = social.clean_name(a.get("name"))
            if not name:
                return {"ok": False, "msg": "Pick a name other players will see."}
            if a.get("age_ok") is not True:
                return {"ok": False, "msg": "Retro-Netplay is for people 13 and older."}
            cfg.update(my_name=name, age_ok=True, setup_done=True)
            core.save_cfg(cfg)
            if self.lobby:
                self.connect(cfg.get("lobby"))           # reconnect with the new name
        elif act == "connect":
            self.connect(a.get("address"))
            addr = cfg.get("lobby")
            if addr and addr not in cfg["lobbies"] and not addr.startswith("127.0.0.1"):
                cfg["lobbies"] = ([addr] + cfg["lobbies"])[:10]
                core.save_cfg(cfg)
        elif act == "post":
            self.post_room(a)
        elif act == "update_room":
            fields = {k: a[k] for k in ("seats", "note", "auto", "public") if k in a}
            if "code" in a and self.myroom and self.myroom.get("mode") == "netplay":
                fields["native"] = {"emu": "dolphin", "code": re.sub(r"[^A-Za-z0-9]", "", str(a["code"]))[:16]}
            self.send({"op": "update", "room": fields})
        elif act == "close_room":
            self.close_room()
        elif act == "join":
            self.send({"op": "join", "room": a.get("room")} if a.get("room") else {"op": "join", "invite": a.get("invite")})
        elif act == "cancel_join":
            self.send({"op": "cancel_join"})
            with self.lock:
                self.seat = None
        elif act == "leave":
            self.send({"op": "leave"})
            with self.lock:
                self.seat = None
                self.stop_proc("player")
                self.stop_proc("emulator")
                self.play_note = ""
        elif act == "answer":
            self.send({"op": "answer", "req": a.get("req"), "accept": a.get("accept") is True})
            with self.lock:
                self.requests = [r for r in self.requests if r["req"] != a.get("req")]
        elif act == "kick":
            self.send({"op": "kick", "player": a.get("player"), "ban": a.get("ban") is True})
        elif act == "chat":
            to = "lobby" if a.get("to") == "lobby" else (self.myroom or (self.seat or {}).get("room") or {}).get("id")
            if to:
                self.send({"op": "chat", "to": to, "text": str(a.get("text", ""))[:300]})
        elif act == "report":
            self.send({"op": "report", "player": a.get("player"), "why": a.get("why"), "text": str(a.get("text", ""))[:300]})
        elif act in ("block", "unblock"):
            pid, name = str(a.get("player", "")), social.clean_name(a.get("name"))
            lst = [b for b in cfg.get("blocked", []) if b["id"] != pid]
            if act == "block" and re.fullmatch(r"[0-9a-f]{24}", pid):
                lst.append({"id": pid, "name": name})
            cfg["blocked"] = lst
            core.save_cfg(cfg)
            if self.lobby:
                self.lobby.send({"op": "block", "ids": [b["id"] for b in lst]})
        elif act == "mod":
            self.send({"op": "mod", "action": a.get("what"), "player": a.get("player"), "minutes": a.get("minutes", 0)})
        elif act == "settings":
            for k in ("chat_filter", "prefer_netplay", "sounds", "use_relay"):
                if isinstance(a.get(k), bool):
                    cfg[k] = a[k]
            if isinstance(a.get("run_lobby"), bool) and a["run_lobby"] != cfg.get("run_lobby"):
                cfg["run_lobby"] = a["run_lobby"]
                if a["run_lobby"]:
                    self.run_own_lobby()
            core.save_cfg(cfg)
        elif act == "add_folder":
            p = str(a.get("path", "")).strip()
            if not p or not os.path.isdir(os.path.expanduser(p)):
                return {"ok": False, "msg": "That folder doesn't exist."}
            if p not in cfg["game_folders"]:
                cfg["game_folders"].append(p)
            threading.Thread(target=self.scan_folders, daemon=True).start()
        elif act == "remove_folder":
            cfg["game_folders"] = [f for f in cfg["game_folders"] if f != a.get("path")]
            threading.Thread(target=self.scan_folders, daemon=True).start()
        elif act == "rescan":
            threading.Thread(target=self.scan_folders, daemon=True).start()
        elif act == "emulator":
            key, path = a.get("emu"), str(a.get("path", "")).strip()
            if key not in core.EMUS:
                return {"ok": False, "msg": "unknown emulator"}
            cfg["paths"][key] = path if path else core.find_emulator(key)
            core.save_cfg(cfg)
        elif act == "play_solo":
            g = self.game(a.get("game"))
            if g and not (p := core.boot_problem(cfg, g)):
                self.launch("emulator", core.solo_cmd(cfg, g))
            elif g:
                return {"ok": False, "msg": p}
        else:
            return {"ok": False, "msg": "unknown action"}
        self.changed()
        return {"ok": True}


# ---------------------------------------------------------------------- the local API
class Api:
    def __init__(self, engine, port=0):
        self.engine, self.port, self.token = engine, port, secrets.token_urlsafe(24)

    def url(self):
        return f"http://127.0.0.1:{self.port}/#t={self.token}"

    async def serve(self, ready=None):
        srv = await asyncio.start_server(self.handle, "127.0.0.1", self.port)
        self.port = srv.sockets[0].getsockname()[1]
        if ready:
            ready(self)
        async with srv:
            await srv.serve_forever()

    async def handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = (lines[0].split(" ") + ["", ""])[:3]
            hdr = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if ":" in l)}
            if hdr.get("host") not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
                return await self.reply(writer, 403, b"wrong host")      # (another site pretending to be us)
            url = urlsplit(target)
            q = parse_qs(url.query)
            path = url.path
            if path.startswith("/api/") or path == "/art":
                tok = hdr.get("x-token") or (q.get("t") or [""])[0]
                if not secrets.compare_digest(tok, self.token):
                    return await self.reply(writer, 403, b"no")
            if method == "GET" and path == "/api/state":
                return await self.reply(writer, 200, json.dumps(self.engine.snapshot()).encode(), "application/json")
            if method == "GET" and path == "/api/events":
                return await self.events(writer)
            if method == "POST" and path == "/api/do":
                n = min(int(hdr.get("content-length", "0") or 0), 65536)
                body = await reader.readexactly(n) if n else b"{}"
                try:
                    a = json.loads(body)
                except ValueError:
                    a = None
                res = await asyncio.get_running_loop().run_in_executor(None, self.engine.do, a) if isinstance(a, dict) else \
                    {"ok": False, "msg": "bad request"}
                return await self.reply(writer, 200, json.dumps(res).encode(), "application/json")
            if method == "GET" and path == "/art":
                p = (q.get("p") or [""])[0]
                if p not in self.engine.art or not os.path.isfile(p):
                    return await self.reply(writer, 404, b"")
                with open(p, "rb") as f:
                    data = f.read(20 << 20)
                return await self.reply(writer, 200, data, mimetypes.guess_type(p)[0] or "application/octet-stream",
                                        cache=True)
            if method == "GET" and re.fullmatch(r"/sounds/[a-z]+\.wav", path):
                f = runtime.BASE / "sounds" / path.rsplit("/", 1)[1]
                if f.is_file():
                    return await self.reply(writer, 200, f.read_bytes(), "audio/wav", cache=True)
            if method == "GET":
                rel = "index.html" if path in ("/", "") else path.lstrip("/")
                f = (UI / rel).resolve()
                if UI.resolve() not in f.parents or not f.is_file():
                    return await self.reply(writer, 404, b"not found")
                return await self.reply(writer, 200, f.read_bytes(), mimetypes.guess_type(str(f))[0] or "text/plain")
            return await self.reply(writer, 404, b"not found")
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, asyncio.LimitOverrunError, ConnectionError, ValueError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def reply(self, writer, status, body, ctype="text/plain; charset=utf-8", cache=False):
        writer.write((f"HTTP/1.1 {status} OK\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                      f"Cache-Control: {'max-age=3600' if cache else 'no-store'}\r\nX-Content-Type-Options: nosniff\r\n"
                      "Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'\r\n"
                      "Connection: close\r\n\r\n").encode() + body)
        await writer.drain()

    async def events(self, writer):
        q = queue.Queue(200)
        self.engine.listeners.append(q)
        loop = asyncio.get_running_loop()
        try:
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\n\r\n")
            writer.write(f"event: state\ndata: {json.dumps(self.engine.snapshot())}\n\n".encode())
            await writer.drain()
            while True:
                try:
                    event, data = await loop.run_in_executor(None, q.get, True, 15)
                    writer.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
                except queue.Empty:
                    writer.write(b": still here\n\n")
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self.engine.listeners.remove(q)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Retro-Netplay engine")
    ap.add_argument("--port", type=int, default=0, help="port for the interface (default: any free one)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the interface in a browser")
    a = ap.parse_args(argv)
    runtime.restore_child_env()
    core.watch_memory()
    eng = Engine()
    eng.start()
    api = Api(eng, a.port)

    def ready(api):
        print(f"RETRO_NETPLAY_UI {api.url()}", flush=True)      # the desktop shell reads this line
        if not a.no_browser:
            webbrowser.open(api.url())
    asyncio.run(api.serve(ready))


if __name__ == "__main__":
    main()
