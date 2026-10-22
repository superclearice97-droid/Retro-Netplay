"""Netplay Matchmaker: one small window to find an opponent for retro games.

Press the big button (or hold your controller hotkey). The app looks for an
opponent two ways at once: the emulator's own netplay (rollback) and a
Parsec-style stream. Whichever finds someone first wins; the other is dropped.
"""
import asyncio
import json
import os
import platform
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import controls
import ink
import instance
import invites
import lan
import library
import mmtls
import portmap
import runtime
import secretbox
import social

HERE = runtime.BASE      # bundled files (icon, notices) whether run from source or the download
CONFIG_PATH = Path.home() / ".netplay-matchmaker" / "config.json"
IS_WIN = platform.system() == "Windows"
LIB_EXT = ".dll" if IS_WIN else (".dylib" if sys.platform == "darwin" else ".so")
NATIVE_GUEST_DELAY = 6  # seconds the guest waits so the host's emulator is listening first
RETRY_SECONDS = 15      # while you play, how often the app retries an unreachable matchmaker

# native=True means the emulator has netplay we can start. Dolphin's must be clicked through by hand.
SYSTEMS = {
    "gba":       {"label": "Game Boy Advance", "emu": "retroarch", "core": "mgba",         "core_name": "mGBA",         "native": True,  "exts": [".gba"]},
    "ps1":       {"label": "PlayStation 1",    "emu": "retroarch", "core": "pcsx_rearmed", "core_name": "PCSX ReARMed", "native": True,  "exts": [".cue", ".pbp", ".m3u", ".chd"]},
    "dreamcast": {"label": "Dreamcast",        "emu": "retroarch", "core": "flycast",      "core_name": "Flycast",      "native": True,  "exts": [".gdi", ".cdi", ".chd"]},
    "gamecube":  {"label": "GameCube / Wii",   "emu": "dolphin",                                                          "native": True,  "exts": [".gcm", ".rvz", ".gcz", ".wbfs", ".iso"]},
    "ps2":       {"label": "PlayStation 2",    "emu": "pcsx2",                                                            "native": False, "exts": [".iso", ".chd", ".cso", ".bin"]},
    "ps3":       {"label": "PlayStation 3",    "emu": "rpcs3",                                                            "native": False, "exts": [".bin", ".iso"]},
}

# label, program names on PATH, glob patterns (relative to common folders)
EMUS = {
    "retroarch": ("RetroArch", ["retroarch"], ["RetroArch*/retroarch.exe", "RetroArch*/RetroArch.app"]),
    "dolphin":   ("Dolphin",   ["dolphin-emu"], ["Dolphin*/Dolphin.exe", "Dolphin-x64/Dolphin.exe"]),
    "pcsx2":     ("PCSX2",     ["pcsx2-qt", "pcsx2"], ["PCSX2*/pcsx2-qt.exe", "PCSX2*/pcsx2.exe"]),
    "rpcs3":     ("RPCS3",     ["rpcs3"], ["rpcs3*/rpcs3.exe", "RPCS3*/rpcs3.exe"]),
}

MANUAL = """HOW TO SET UP CONTROLLERS FOR 2 PLAYERS

1. Connect both controllers BEFORE you open a game.
   - USB: plug each one into the PC.
   - Bluetooth: Windows Settings > Bluetooth & devices > Add device. Put the
     controller in pairing mode (usually hold its pair or share button until the
     light flashes) and pick it from the list.

2. Check that your PC sees both.
   Press Start, type "Set up USB game controllers" and open it. You should see
   two controllers. Select one and click Properties to test its buttons.

3. Set them up in this app (click "Controllers..." on the main window).
   - Player 1 and Player 2 are always on. Leave them on "Auto", or pick which
     controller belongs to which player.
   - Click "Map..." for a player and press each button when asked. It takes
     about 30 seconds. If the controller already works, you can skip this and
     the emulator's own detection is used.

4. More than two people?
   Tick Player 3 and Player 4. Map one controller, click "Copy" on it, then
   "Paste" on the others, or use "Copy Player 1 to everyone".

5. Not RetroArch? (Dolphin, PCSX2, RPCS3)
   Those emulators keep their own controller settings, so set each player's
   controller inside the emulator. Make sure Port 1 and Port 2 use DIFFERENT
   controllers.

6. Something is still wrong?
   Close everything, unplug all controllers, plug Player 1's in first and then
   Player 2's, and start the app again.
"""


DEFAULT_CFG = {
    "server_host": "127.0.0.1", "server_port": 7777, "run_server": True, "auto_mm": True, "server_name": "",
    "parsec_peer_id": "", "stream_backend": "own", "paths": {}, "games": [], "last_game": 0,
    "hotkey": [6, 7], "hold": 3.0, "hold_v2": True,
    "slots": controls.default_slots(), "controller_check_done": False,
    "setup_done": False, "game_folders": [], "folder_answers": {}, "auto_ports": True, "port_status": {},
    "secret": "", "my_name": "", "friends": [], "auto_queue": True,
}
invites.fill_defaults(DEFAULT_CFG)


# ----------------------------------------------------------------- config / detection
def load_cfg():
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    stored = {}
    try:
        stored = json.loads(CONFIG_PATH.read_text())
        cfg.update(stored)
    except (OSError, ValueError, TypeError):
        stored = {}
    cfg["slots"] = controls.normalize(cfg["slots"])
    invites.fill_defaults(cfg)
    if stored and not stored.get("hold_v2"):   # older default was 1 s; Start + Select held 3 s is the shortcut now
        if float(cfg.get("hold", 1.0)) == 1.0:
            cfg["hold"] = 3.0
        cfg["hold_v2"] = True
    social.ensure_identity(cfg)
    plain = False
    for k in SECRET_KEYS:
        stored = cfg["social"].get(k, "")
        cfg["social"][k] = secretbox.reveal(k, stored)
        plain = plain or (stored and not stored.startswith((secretbox.DPAPI, secretbox.KEYCHAIN)))
    if plain:
        save_cfg(cfg)   # move secrets saved by older versions into the secure store
    return cfg


SECRET_KEYS = ("discord_webhook", "telegram_token")


def save_cfg(cfg):
    out = json.loads(json.dumps(cfg))
    for k in SECRET_KEYS:
        if k in out.get("social", {}):
            out["social"][k] = secretbox.protect(k, out["social"][k])
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        # created owner-only from the start, then swapped in, so the file is never briefly world-readable
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(out, indent=2))
        os.replace(tmp, CONFIG_PATH)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass
    except OSError:
        pass


FLATPAK = {"retroarch": "org.libretro.RetroArch", "dolphin": "org.DolphinEmu.dolphin-emu",
           "pcsx2": "net.pcsx2.PCSX2", "rpcs3": "net.rpcs3.RPCS3"}


def emu_argv(exe):
    """A path to a program, or a command such as 'flatpak run org.libretro.RetroArch'."""
    if not exe or Path(exe).exists():
        return [exe]
    try:
        return shlex.split(exe)
    except ValueError:
        return [exe]


def emu_ok(exe):
    argv = emu_argv(exe)
    return bool(exe) and (Path(argv[0]).exists() or bool(shutil.which(argv[0])))


def find_flatpak(key):
    if IS_WIN or not shutil.which("flatpak"):
        return ""
    try:
        r = subprocess.run(["flatpak", "info", FLATPAK[key]], capture_output=True, timeout=6)
        return f"flatpak run {FLATPAK[key]}" if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def find_emulator(key):
    _, names, globs = EMUS[key]
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    roots = [Path.home(), Path.home() / "Downloads", Path.home() / "Desktop"]
    if IS_WIN:
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
            if os.environ.get(env):
                roots.append(Path(os.environ[env]))
        roots += [Path("C:/"), Path("D:/")]
    else:
        roots += [Path("/opt"), Path("/Applications")]
    fp = find_flatpak(key)
    if fp:
        return fp
    for r in roots:
        for g in globs:
            for pat in (g, "*/" + g):
                try:
                    hit = next(iter(sorted(r.glob(pat))), None)
                except OSError:
                    hit = None
                if hit:
                    return str(hit)
    return ""


def autodetect(cfg):
    for key in EMUS:
        if not emu_ok(cfg["paths"].get(key, "")):
            cfg["paths"][key] = find_emulator(key)


def core_dirs(cfg):
    dirs = []
    if cfg["paths"].get("cores"):
        dirs.append(Path(cfg["paths"]["cores"]))
    ra = cfg["paths"].get("retroarch")
    if ra:
        dirs.append(Path(ra).parent / "cores")
    dirs += [Path.home() / ".config/retroarch/cores",
             Path.home() / ".var/app/org.libretro.RetroArch/config/retroarch/cores", Path("/usr/lib/libretro"),
             Path("/usr/lib64/libretro"), Path("/usr/lib/x86_64-linux-gnu/libretro")]
    if IS_WIN and os.environ.get("APPDATA"):
        dirs.append(Path(os.environ["APPDATA"]) / "RetroArch/cores")
    return dirs


def find_core(cfg, core):
    for d in core_dirs(cfg):
        p = d / f"{core}_libretro{LIB_EXT}"
        if p.exists():
            return str(p)
    return ""


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def game_key(g):
    return f"{g['system']}:{slug(g['name'])}"


def checklist(cfg, g, ncontrollers=None):
    """[(status, text)] where status is True (ok), False (blocking), None (heads-up)."""
    s = SYSTEMS[g["system"]]
    emu_label = EMUS[s["emu"]][0]
    items = []
    exe = cfg["paths"].get(s["emu"], "")
    items.append((emu_ok(exe), f"{emu_label} found" if emu_ok(exe) else f"{emu_label} not found. Open Settings and pick it."))
    if s.get("core"):
        ok = bool(find_core(cfg, s["core"]))
        items.append((ok, f"{s['core_name']} core installed" if ok else
                      f"{s['core_name']} core missing. In RetroArch: Load Core > Download a Core > {s['core_name']}."))
    items.append((Path(g["rom"]).exists(), "Game file found" if Path(g["rom"]).exists() else "Game file moved or deleted. Remove and re-add it."))
    stream_on, can_host, _ = stream_offer(cfg)
    own = cfg.get("stream_backend", "own") == "own"
    if s["native"]:
        items.append((True, "Rollback netplay available" + (" (you click through Dolphin's NetPlay menu)" if s["emu"] == "dolphin" else "")))
        if own:
            ok, note = own_host_ready()
            items.append((True if ok else None, ("Streaming backup on. " if ok else "Optional streaming backup: ") + note))
        else:
            items.append((True if stream_on else None, "Parsec backup on" if stream_on else "Parsec ID not set. Optional backup, add it in Settings."))
    elif own:
        ok, note = own_host_ready()
        # Not being able to host is fine: you can still join someone else's stream.
        items.append((True if ok else None, note))
    else:
        items.append((stream_on, "Parsec streaming on" if stream_on else
                      f"{s['label']} has no netplay, so it needs your Parsec ID in Settings."))
    if own and "v" in _guest_cache and not _guest_cache["v"][0]:      # (checked in the background at start)
        first = next((l[4:].strip() for l in _guest_cache["v"][1].splitlines() if l.startswith("[X")), "")
        items.append((None, "Joining a friend's stream won't work yet: " + (first or _guest_cache["v"][1][:160]).rstrip(".")
                      + ". Settings > Streaming check shows how to fix it."))
    if ncontrollers is not None:
        if ncontrollers >= 2:
            items.append((True, f"{ncontrollers} controllers detected (Player 1 and Player 2 ready)"))
        elif ncontrollers == 1:
            items.append((None, "1 controller detected. Player 2 stays open: a friend can plug in or turn on a controller any time."))
        else:
            items.append((None, "No controller detected. Plug one in, or set keyboard keys inside the emulator."))
    return items


_own_cache = {}


def stream_doctor():
    """-> (ok, full report text). Asks the computer's own Python (which has GStreamer) to check streaming."""
    if platform.system() != "Linux":
        return False, "Hosting a built-in stream works from Linux only for now. You can still join a friend's stream."
    py = runtime.host_python()
    if not py:
        return False, "Hosting a stream needs Python 3 and GStreamer on this computer. You can still join a friend's stream."
    try:
        r = subprocess.run([py, str(runtime.host_script()), "--doctor"], capture_output=True, text=True, timeout=25)
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Couldn't check streaming tools ({e})"


def own_host_ready(refresh=False):
    """-> (ok, note). Can THIS computer host the built-in stream? (Linux + GStreamer + encoder + virtual pad)"""
    if refresh or "v" not in _own_cache:
        ok, report = stream_doctor()
        if ok:
            _own_cache["v"] = (True, "Built-in streaming ready (you can host)")
        else:
            first = next((l[4:].strip() for l in report.splitlines() if l.startswith("[X")), "")
            if not first:
                _own_cache["v"] = (False, report.splitlines()[0] if report else "Built-in streaming isn't ready.")
            else:
                _own_cache["v"] = (False, f"Hosting PS2/PS3 streams: {first.rstrip('.')}. Joining a friend's stream "
                                          "works without it. Settings > Streaming check shows how to fix it.")
    return _own_cache["v"]


_guest_cache = {}


def guest_ready(refresh=False):
    """-> (ok, report). Can THIS computer show a friend's built-in stream? Asks each way of starting the player
    (runtime.guest_commands) for its own --check and remembers the first that works."""
    if refresh or "v" not in _guest_cache:
        cmds, reports = runtime.guest_commands(), []
        _guest_cache["cmd"] = cmds[0] if cmds else []
        for cmd in cmds:
            try:
                r = subprocess.run(cmd + ["--check"], capture_output=True, text=True, timeout=30)
                ok, report = r.returncode == 0, (r.stdout or r.stderr or "").strip()
            except (OSError, subprocess.SubprocessError) as e:
                ok, report = False, f"Couldn't check the player ({e})"
            if ok:
                _guest_cache.update(v=(True, report), cmd=cmd)
                break
            reports.append(report)
        else:
            _guest_cache["v"] = (False, "\n\n".join(reports) if reports else
                                 "The player window is missing from this copy of Retro-Netplay. Download it again.")
    return _guest_cache["v"]


def stream_offer(cfg):
    """-> (lane_enabled, can_host, info) for the stream lane under the chosen backend."""
    if cfg.get("stream_backend", "own") == "own":
        # sig "tcp": this app sets up streams over the matchmaker connection and joins in its own player window
        return True, own_host_ready()[0], {"backend": "own", "sig": "tcp"}
    pid = cfg["parsec_peer_id"].strip()
    return bool(pid), True, {"peer_id": pid, "backend": "parsec"}


def readiness(cfg, g):
    """-> (lanes, first_problem)"""
    items = checklist(cfg, g)
    for ok, text in items:
        if ok is False:
            return [], text
    s = SYSTEMS[g["system"]]
    lanes = (["native"] if s["native"] else []) + (["stream"] if stream_offer(cfg)[0] else [])
    return lanes, ""


RA_CMD_PORT = 55355


def ra_cmd(cmd, port=None):
    """Send one RetroArch network command (this computer only). Used to start hosting and show messages."""
    import socket as _s
    try:
        with _s.socket(_s.AF_INET, _s.SOCK_DGRAM) as u:
            u.sendto(cmd.replace("\n", " ").encode(), ("127.0.0.1", port or RA_CMD_PORT))
    except OSError:
        pass


def write_ra_config(cfg):
    path = CONFIG_PATH.parent / "retroarch-players.cfg"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # network commands let the app start hosting in a game that's already running, and show messages in it
        path.write_text(controls.retroarch_config(cfg["slots"])
                        + f'network_cmd_enable = "true"\nnetwork_cmd_port = "{RA_CMD_PORT}"\n')
        return str(path)
    except OSError:
        return ""


PORTS = portmap.PortMapper()


def needed_ports(cfg):
    """Emulators set up on this PC that host netplay, and so need a port open on the router."""
    return [k for k in portmap.APP_PORTS if emu_ok(cfg["paths"].get(k, ""))]


def _local_server(host):
    h = (host or "").strip().lower()
    return h in ("", "localhost") or h.startswith(("127.", "10.", "192.168.")) or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", h)


def refresh_ports(cfg):
    """Open the hosting ports on the router (if allowed) and check them through the matchmaker.
    -> {emulator: {"state": open|closed|mapped|failed, "message": plain words}}; also saved in cfg["port_status"]."""
    out = {}
    if not cfg.get("auto_ports", True):
        cfg["port_status"] = out
        return out
    for key in needed_ports(cfg):
        port, proto, label = portmap.APP_PORTS[key]
        r = PORTS.open(port, proto)
        st = {"state": "mapped" if r["ok"] else "failed", "message": f"{label}: " + r["message"]}
        if key == "dolphin" and not r["ok"]:
            # Dolphin connects through its own traversal server (a host code), so no open port is needed
            st = {"state": "traversal", "message": "Dolphin netplay: your router didn't open a port, and that's fine. "
                  "In Dolphin's NetPlay window choose \"Traversal Server\" and share the host code it shows."}
        if proto == "TCP" and not _local_server(cfg.get("server_host")) and not cfg.get("run_server"):
            c = portmap.check_port(cfg["server_host"], int(cfg["server_port"]), port)
            if c["open"] is not None:
                st = {"state": "open" if c["open"] else "closed", "message": f"{label}: " + c["message"]}
        out[key] = st
    cfg["port_status"] = out
    return out


def can_host_native(cfg, g):
    """Only say we can't host when a test proved the port is closed; otherwise keep the old behaviour."""
    emu = SYSTEMS[g["system"]]["emu"]
    return (cfg.get("port_status") or {}).get(emu, {}).get("state") != "closed"


def safe_peer_id(peer):
    """A Parsec peer ID from the opponent, or "" if it isn't one (never pass odd text to a URL handler)."""
    v = str(peer.get("peer_id", ""))
    return v if re.fullmatch(r"[A-Za-z0-9]{1,64}", v) else ""


def safe_ip(peer, matchmaker=""):
    """The opponent's IP address as the matchmaker reported it, or "" if it isn't an IP address.
    at_matchmaker: the host is on the matchmaker's own network, so it's reached at the matchmaker's address."""
    import ipaddress
    if peer.get("at_matchmaker") and matchmaker and not lan.is_loopback(matchmaker):
        try:
            import socket as _s
            return str(ipaddress.ip_address(_s.gethostbyname(matchmaker)))
        except (OSError, ValueError):
            pass
    try:
        return str(ipaddress.ip_address(str(peer.get("ip", ""))))
    except ValueError:
        return ""


def build_cmd(cfg, g, lane, role, peer):
    """-> ('link', url) or ('run', [argv...])"""
    s = SYSTEMS[g["system"]]
    exe, rom = cfg["paths"][s["emu"]], g["rom"]
    if lane == "stream" and role == "guest":
        return "link", f"parsec://peer_id={safe_peer_id(peer)}"
    kind, argv = _build(cfg, g, s, exe, rom, lane, role, peer)
    return kind, emu_argv(exe) + argv[1:]


def _build(cfg, g, s, exe, rom, lane, role, peer):
    if s["emu"] == "retroarch":
        core = find_core(cfg, s["core"])
        extra = ["--appendconfig", p] if (p := write_ra_config(cfg)) else []
        if lane == "native":
            return "run", [exe, "-L", core, rom] + extra + (["--host"] if role == "host" else ["--connect", safe_ip(peer)])
        return "run", [exe, "-f", "-L", core, rom] + extra
    if s["emu"] == "dolphin":
        # Dolphin's netplay is started from its own window (Tools > NetPlay), so open the window, not just the game
        return "run", [exe] if lane == "native" else [exe, "-e", rom]
    if s["emu"] == "pcsx2":
        return "run", [exe, "-batch", "-fullscreen", "--", rom]
    return "run", [exe, "--no-gui", rom]  # rpcs3


def solo_cmd(cfg, g):
    """The command that just starts the game (no netplay yet), so you play while you wait in the queue."""
    s = SYSTEMS[g["system"]]
    exe, rom = cfg["paths"].get(s["emu"], ""), g["rom"]
    if s["emu"] == "retroarch":
        argv = [exe, "-L", find_core(cfg, s["core"]), rom]
        if p := write_ra_config(cfg):
            argv += ["--appendconfig", p]
    elif s["emu"] == "dolphin":
        argv = [exe, "-b", "-e", rom]
    elif s["emu"] == "pcsx2":
        argv = [exe, "-batch", "-fullscreen", "--", rom]
    else:
        argv = [exe, "--no-gui", rom]
    return emu_argv(exe) + argv[1:]


def boot_problem(cfg, g):
    """'' if the game can start on this computer, else what to fix."""
    s = SYSTEMS[g["system"]]
    label = EMUS[s["emu"]][0]
    if not emu_ok(cfg["paths"].get(s["emu"], "")):
        return f"{label} wasn't found. Open Settings and pick it (or Auto-detect emulators)."
    if s.get("core") and not find_core(cfg, s["core"]):
        return f"The {s['core_name']} core is missing. In RetroArch: Load Core > Download a Core > {s['core_name']}."
    if not Path(g["rom"]).exists():
        return "The game file moved or was deleted. Click Game folders... > Scan again."
    return ""


_local_mm = {"port": None, "ok": False, "err": "", "id": ""}


def ensure_local_server(cfg, note):
    """Start the matchmaker inside this app when the player chose to run it here (once)."""
    if cfg.get("run_server") and _local_mm["port"] != int(cfg["server_port"]):
        _local_mm["port"] = int(cfg["server_port"])
        start_local_server(int(cfg["server_port"]), note)


def no_matchmaker_hint(cfg):
    host = str(cfg.get("server_host", "")).strip()
    if host in ("", "127.0.0.1", "localhost") and not cfg.get("run_server"):
        return ("No matchmaker is set yet. In Settings, type the address a friend gave you, "
                "or tick \"Run the matchmaker on this PC\".")
    return ""


# ----------------------------------------------------------------- matchmaking engine (no GUI)
def join_problem(can, g):
    """'' if joining a friend who hosts g with abilities `can` should work from this computer, else why not."""
    if not can:
        return ""                                   # an older app that doesn't say: just try
    s = SYSTEMS[g["system"]]
    if can.get("stream"):
        return ""
    if can.get("native") and s["native"]:
        return ""
    if s["native"]:
        return ("{name} can't host " + g["name"] + " right now: their router port is closed and they can't stream it. "
                "Try you hosting instead (they join you).")
    return ("{name}'s computer can't stream " + g["name"] + " yet. On their computer: Settings > Streaming check "
            "shows what to install.")


class MatchRefused(Exception):
    """The matchmaker answered, but said no (for example: you two can't connect for this game)."""


def host_abilities(cfg, g):
    """What this computer can host for game g right now (friends' apps use it to see whether joining works)."""
    if not g:
        return {"native": False, "stream": False}
    s = SYSTEMS[g["system"]]
    lanes, _ = readiness(cfg, g)
    return {"native": "native" in lanes and can_host_native(cfg, g),
            "stream": "stream" in lanes and stream_offer(cfg)[1]}


class Engine:
    """Search -> match -> launch. emit(state, message, clip=None) reports progress."""

    def __init__(self, get_cfg, emit):
        self.get_cfg, self.emit = get_cfg, emit
        self.state, self.task, self.proc = "idle", None, None
        self.pending_join, self.last_invite = None, None
        self.stream_proc, self.mm = None, ("127.0.0.1", 7777)
        self.ingame = None          # {"key", "game", "proc"}: a game this app started before matching
        self.on_boot = None         # called before a game starts (the window starts its own matchmaker here)
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def set(self, state, msg, clip=None):
        if state != "searching" and self.last_invite:
            self.last_invite = None
            self.emit("link", "", None)  # the invite is used up or cancelled
        self.state = state
        self.emit(state, msg, clip)

    def toggle(self):
        self.loop.call_soon_threadsafe(self._toggle)

    def _toggle(self):
        if self.state == "idle":
            self.task = self.loop.create_task(self.run())
        else:
            self.stop()

    # ---- play first, then queue (RetroArch games); the controller hotkey
    def play_game(self):
        self.loop.call_soon_threadsafe(self._play_game)

    def _play_game(self):
        if self.state != "idle":
            return self.stop()
        self.task = self.loop.create_task(self.boot_and_queue())

    def hotkey(self):
        self.loop.call_soon_threadsafe(self._hotkey)

    def _hotkey(self):
        if self.state == "searching":
            self.stop()                                   # leave the queue; a running game keeps running
        elif self.state == "session" and self.ingame_alive() and not self.matched:
            if self.task and not self.task.done():
                self.task.cancel()
            self.task = self.loop.create_task(self.run(ingame=True))
        elif self.state == "idle":
            self._play_game()

    def ingame_alive(self):
        return bool(self.ingame) and self.ingame["proc"].returncode is None

    matched = False

    async def boot_and_queue(self):
        cfg = self.get_cfg()
        games = cfg["games"]
        if not games:
            return self.set("idle", "Add a game first (Game folders or Add one).")
        g = games[min(cfg["last_game"], len(games) - 1)]
        s = SYSTEMS[g["system"]]
        problem = boot_problem(cfg, g)
        if problem:
            return self.set("idle", f"Can't start {g['name']}: {problem}")
        if self.on_boot:
            self.on_boot(cfg)                             # e.g. start this PC's own matchmaker
        try:
            proc = await asyncio.create_subprocess_exec(*solo_cmd(cfg, g))
        except OSError as e:
            return self.set("idle", f"Couldn't start {EMUS[s['emu']][0]}: {e}")
        self.ingame, self.matched = {"key": game_key(g), "game": g, "proc": proc}, False
        self.loop.create_task(self._watch_ingame(proc))
        if not cfg.get("auto_queue", True):
            return self.set("session", f"Playing {g['name']}. Hold Start + Select for 3 seconds to look for an opponent.")
        await self.run(ingame=True)

    async def _watch_ingame(self, proc):
        await proc.wait()
        if self.ingame and self.ingame["proc"] is proc:
            self.ingame = None
            if self.task and not self.task.done() and self.state == "searching":
                self.task.cancel()
            if self.state in ("searching", "session"):
                self.set("idle", "You closed the game. Press Play to start again.")

    def join_refusal(self, game):
        """Why a friend can't join this computer for `game` right now ('no_game', 'busy'), or ''."""
        if not any(game_key(x) == game for x in self.get_cfg()["games"]):
            return "no_game"
        if self.state == "session" and self.matched:
            return "busy"
        return ""

    def start_call(self, d):
        """A friend's call was accepted: meet them in the call's private room."""
        self.loop.call_soon_threadsafe(self._start_call, d)

    def _start_call(self, d):
        cfg = self.get_cfg()
        if not any(game_key(x) == d["game"] for x in cfg["games"]):
            return self.emit(self.state, f"You don't have that game in your list, so {d['with']} can't join.", None)
        if self.task and not self.task.done() and self.state == "searching":
            self.task.cancel()
        self.pending_join = {"game": d["game"], "host": cfg["server_host"], "port": cfg["server_port"], "code": d["code"],
                             "with": d["with"], "as": d.get("as", "")}
        ingame = self.ingame_alive() and self.ingame["key"] == d["game"]
        self.task = self.loop.create_task(self.run(ingame=ingame))

    def stop(self):
        was = self.state
        if was == "searching" and self.ingame_alive():
            if self.task and not self.task.done():
                self.task.cancel()
            return self.set("session", f"You left the queue. Still playing {self.ingame['game']['name']}: "
                                       "hold your controller hotkey to look for an opponent again.")
        self.state = "idle"
        if self.task and not self.task.done():
            self.task.cancel()
        if self.ingame_alive():
            try:
                self.ingame["proc"].terminate()
            except ProcessLookupError:
                pass
        self.ingame = None
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
        self.proc = None
        if self.stream_proc and self.stream_proc.returncode is None:
            try:
                self.stream_proc.terminate()
            except ProcessLookupError:
                pass
        self.stream_proc = None
        self.set("idle", "Search cancelled." if was == "searching" else "Session ended. Press the button to find a new opponent.")

    def join(self, parsed):
        self.loop.call_soon_threadsafe(self._join, parsed)

    def _join(self, parsed):
        if self.state == "idle":
            self.pending_join = parsed
            self.task = self.loop.create_task(self.run())
        else:
            self.emit(self.state, "Finish or cancel what you're doing first, then open the invite again.", None)

    def announce_now(self):
        if not self.last_invite:
            return self.emit(self.state, "Start a search first, then you can post your invite.", None)
        g, link = self.last_invite
        threading.Thread(target=self._announce, args=(self.get_cfg(), g, link, True), daemon=True).start()

    def _announce(self, cfg, g, link, force=False):
        results = invites.announce(cfg, g["name"], link, force=force)
        save_cfg(cfg)
        good = [n for n, ok, _ in results if ok]
        bad = [f"{n}: {note}" for n, ok, note in results if not ok]
        text = ("Posted your invite to " + ", ".join(good) + ". " if good else "") + "; ".join(bad)
        prefix = f"Looking for an opponent for {g['name']}... " if self.state == "searching" else ""
        self.emit(self.state, prefix + text, None)

    def share_invite(self, cfg, g, code):
        link = invites.build_link(cfg, game_key(g), code)
        self.last_invite = (g, link)
        self.emit("link", link, link)  # the window shows it and copies it
        note = "" if invites.link_is_shareable(cfg) else (
            " (This link only works on your own network until you set a public address in Invites.)")
        self.emit("searching", f"Looking for an opponent for {g['name']}... Invite link copied. Or tell your friend "
                               f"the code {invites.pretty_code(code)} (they type it under Enter code).{note}", None)
        if cfg["invite"]["auto_announce"] and invites.link_is_shareable(cfg):
            threading.Thread(target=self._announce, args=(cfg, g, link), daemon=True).start()

    async def run(self, ingame=False):
        cfg = self.get_cfg()
        self.matched = False
        join, self.pending_join = self.pending_join, None
        games = cfg["games"]
        if ingame and self.ingame_alive() and not join:
            g = self.ingame["game"]
            inv = cfg["invite"]
            host, port = cfg["server_host"], int(cfg["server_port"])
            code, only = (invites.new_code() if inv["enabled"] else ""), not inv["open_to_randoms"]
        elif join:
            g = next((x for x in games if game_key(x) == join["game"]), None)
            if g is None:
                guess = join["game"].split(":", 1)[1].replace("-", " ").title()
                return self.set("idle", f"You don't have this game yet. Use Add game and name it \"{guess}\", "
                                        "then open the invite again.")
            host, port, code, only = join["host"], int(join["port"]), join["code"], True
        else:
            if not games:
                return self.set("idle", "Add a game first (Add game button).")
            g = games[min(cfg["last_game"], len(games) - 1)]
            inv = cfg["invite"]
            host, port = cfg["server_host"], int(cfg["server_port"])
            code, only = (invites.new_code() if inv["enabled"] else ""), not inv["open_to_randoms"]
        lanes, problem = readiness(cfg, g)
        if not lanes:
            return self.set("idle", "Can't start yet: " + problem)
        who = (join or {}).get("with") or "your friend"
        self.set("searching", f"{'Joining ' + who + ' for' if join else 'In the queue for'} {g['name']}..."
                 + (" Hold your controller hotkey to leave." if ingame else ""))
        _, stream_host_ok, stream_info = stream_offer(cfg)
        self.mm = (host, port)
        request = {
            "v": 1, "op": "search", "game": game_key(g), "lanes": lanes,
            "info": {"native": {}, "stream": stream_info},
            "can_host": {"native": can_host_native(cfg, g), "stream": stream_host_ok},
            "client": f"desktop-{platform.system().lower()}",
        }
        # a friend's join: the one who accepted (callee) hosts, the one who asked connects to them
        if join and join.get("as") in ("caller", "callee"):
            prefer = join["as"] == "callee"
        else:
            prefer = ingame and can_host_native(cfg, g)
        request.update(social.search_fields(cfg, prefer_host=prefer))
        if code:
            request["invite"], request["invite_only"] = code, only
        writer = None
        try:
            # encrypted when the matchmaker offers it (see mmtls.py)
            sock, tls = await asyncio.get_running_loop().run_in_executor(None, mmtls.negotiate, host, port)
            sock.setblocking(False)
            reader, writer = await asyncio.wait_for(asyncio.open_connection(
                sock=sock, **({"ssl": tls, "server_hostname": host} if tls else {})), 8)
            if tls:
                mmtls.tls_done(host)
            writer.write((json.dumps(request) + "\n").encode())
            await writer.drain()
            if code and not join:
                self.share_invite(cfg, g, code)
            while True:  # skip keep-alive pings until we're matched
                line = await reader.readline()
                if not line:
                    raise ConnectionError("the matchmaker hung up")
                msg = json.loads(line)
                if msg.get("op") == "matched":
                    match = msg
                    break
                if msg.get("op") == "error":
                    raise MatchRefused(msg.get("msg", "the matchmaker refused the request"))
        except asyncio.CancelledError:
            raise
        except MatchRefused as e:
            return self.set("session" if self.ingame_alive() else "idle", str(e))
        except Exception as e:
            why = no_matchmaker_hint(cfg) or (f"Couldn't reach the matchmaker at {host}:{port} ({e or type(e).__name__}). "
                                              "Check the address in Settings" + (" or ask for a new invite." if join else "."))
            if ingame and self.ingame_alive() and not join:
                # keep playing; try the queue again in a little while (the hotkey leaves, as usual)
                self.set("searching", f"Playing {g['name']} while we wait. {why} Trying again in {RETRY_SECONDS} s.")
                await asyncio.sleep(RETRY_SECONDS)
                if self.ingame_alive() and self.state == "searching":
                    return await self.run(ingame=True)
                return
            return self.set("idle", why)
        finally:
            if writer:
                writer.close()
        await self.play(cfg, g, match)

    async def play(self, cfg, g, match):
        lane, role, peer = match["lane"], match["role"], match["peer"]
        if lane == "native" and role == "guest":
            peer = {**peer, "ip": safe_ip(peer, self.mm[0])}
        s = SYSTEMS[g["system"]]
        sess = match.get("session") if isinstance(match.get("session"), dict) else None
        where = "emulator netplay" if lane == "native" else ("built-in stream" if sess else "Parsec stream")
        other = peer.get("client", "")
        name = social.clean_name(peer.get("name")) or ("your friend" if match.get("via") == "invite" else "an opponent")
        friend = f"Matched with {name}" + (" (friend)" if peer.get("friend") else "") + "! "
        self.matched = True
        self.set("session", f"{friend}Playing via {where}. You are the {role}." + (f" (They're on {other}.)" if other.startswith("android") else ""))
        if lane == "native" and s["emu"] == "retroarch" and self.ingame_alive():
            if role == "host":
                # the game is already running: just start hosting in it
                ra_cmd("NETPLAY_HOST_TOGGLE")
                ra_cmd(f"SHOW_MSG Retro-Netplay: matched with {name}! You're hosting; they're joining you.")
                self.set("session", f"{friend}You're hosting {g['name']}; {name} is joining you.")
                await self.ingame["proc"].wait()
                return
            # guest: restart the game connected to the host
            ra_cmd(f"SHOW_MSG Retro-Netplay: matched with {name}! Reconnecting to their game...")
            proc, self.ingame = self.ingame["proc"], None
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            await proc.wait()
        keep = lane == "stream" and sess and role == "host" and self.ingame_alive()   # stream the game that's running
        if self.ingame_alive() and not keep:
            if s["emu"] == "dolphin" and lane == "native":
                self.set("session", f"{friend}Closing the game to open Dolphin's NetPlay window...")
            proc, self.ingame = self.ingame["proc"], None
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            await proc.wait()
        if lane == "stream" and sess:
            return await self.play_own_stream(cfg, g, role, sess, friend, peer)
        kind, what = build_cmd(cfg, g, lane, role, peer)
        if kind == "link":
            pid = safe_peer_id(peer)
            if not pid:
                return self.set("idle", "Your opponent didn't share a valid Parsec ID. Press the button to search again.")
            open_app_link(what)
            return self.set("session", f"Opening Parsec. If it doesn't connect, join peer ID {pid} in Parsec "
                                       f"(copied to your clipboard).", clip=pid)
        if lane == "native" and role == "guest" and not safe_ip(peer):
            return self.set("idle", "The matchmaker didn't send a valid address for your opponent. Search again.")
        if lane == "native" and role == "guest":
            self.set("session", f"Opponent found! Waiting {NATIVE_GUEST_DELAY}s for the host to start...")
            await asyncio.sleep(NATIVE_GUEST_DELAY)
        note = ""
        if s["emu"] == "dolphin" and lane == "native":
            note = (" In Dolphin: Tools > NetPlay > Host, pick the game and share the code." if role == "host"
                    else " In Dolphin: Tools > NetPlay > Connect, and enter the host's code.")
        self.set("session", f"Starting {EMUS[s['emu']][0]}...{note}")
        try:
            self.proc = await asyncio.create_subprocess_exec(*what)
        except OSError as e:
            return self.set("idle", f"Couldn't start {EMUS[s['emu']][0]}: {e}")
        self.set("session", f"Playing {g['name']} ({where}).{note}")
        await self.proc.wait()
        if self.state == "session":
            self.set("idle", "Session ended. Press the button to find a new opponent.")


    async def play_as_guest(self, host, port, sess, friend, peer):
        """Open the player window on the friend's stream and wait until it closes. Never a web browser."""
        key = str(sess.get("key", ""))
        if sess.get("sig") != "tcp":
            return self.set("idle", "The matchmaker you're using is an older version that can't set up streams with this "
                                    "app. Update Retro-Netplay on the computer that runs the matchmaker, then press Join again.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", key):
            return self.set("idle", "The matchmaker sent a stream key that doesn't look right. Press Join again.")
        if "cmd" not in _guest_cache:
            await asyncio.get_running_loop().run_in_executor(None, guest_ready)
        cmd = list(_guest_cache.get("cmd") or [])
        if not cmd:
            return self.set("idle", "The player that shows your friend's game is missing from this copy of Retro-Netplay. "
                                    "Download the app again.")
        name = social.clean_name(peer.get("name"))
        cmd += ["--mm", f"{host}:{port}", f"--key={key}"] + ([f"--name={name}"] if name else []) + (
            [] if self.get_cfg().get("use_relay", True) else ["--no-relay"])
        log_path = CONFIG_PATH.parent / "stream-guest.log"      # what the player did, for "why didn't it work?"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_f = open(log_path, "w")
        except OSError:
            log_f = None
        try:
            self.stream_proc = await asyncio.create_subprocess_exec(*cmd, stdout=log_f or asyncio.subprocess.DEVNULL,
                                                                    stderr=asyncio.subprocess.STDOUT)
        except OSError as e:
            return self.set("idle", f"Couldn't open the player window: {e}")
        self.set("session", f"{friend}Your friend's game opens in its own window. Your controller is Player 2 "
                            "(or use the keyboard). F11: full screen. Esc twice: leave.")
        await self.stream_proc.wait()
        if log_f:
            log_f.close()
        why = guest_result(log_path)
        self.stream_proc = None
        if self.state == "session":
            self.set("idle", (f"{why} " if why and why != "you left" else "") + "Press the button to play again.")

    async def play_own_stream(self, cfg, g, role, sess, friend="", peer=None):
        host, port = self.mm
        if role == "guest":
            return await self.play_as_guest(host, port, sess, friend, peer or {})
        if sess.get("sig") != "tcp":
            return self.set("idle", "The matchmaker you're using is an older version that can't set up streams with this "
                                    "app. Update Retro-Netplay on the computer that runs the matchmaker, then try again.")
        s = SYSTEMS[g["system"]]
        if self.ingame_alive():
            self.proc, self.ingame = self.ingame["proc"], None     # the game is already on screen: stream it
            self.set("session", f"{friend}Starting the stream of the game you're playing...")
        else:
            _, what = build_cmd(cfg, g, "stream", "host", {})
            self.set("session", f"{friend}Starting {EMUS[s['emu']][0]} and the stream...")
            try:
                self.proc = await asyncio.create_subprocess_exec(*what)
            except OSError as e:
                return self.set("idle", f"Couldn't start {EMUS[s['emu']][0]}: {e}")
            await asyncio.sleep(NATIVE_GUEST_DELAY)   # give the emulator a moment to open its window
        # --key=...: a key may start with "-", which would otherwise be read as an option
        cmd = [runtime.host_python(), str(runtime.host_script()), "--mm", f"{host}:{port}",
               f"--key={sess.get('key', '')}", "--slot", "2"] + ([] if cfg.get("use_relay", True) else ["--no-relay"])
        tell = lambda text: tell_guest(host, port, sess.get("key", ""), text)
        log_path = CONFIG_PATH.parent / "stream-host.log"     # what the stream did, for "why didn't it work?"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_f = open(log_path, "w")
        except OSError:
            log_f = None
        try:
            self.stream_proc = await asyncio.create_subprocess_exec(*cmd, stdout=log_f or asyncio.subprocess.DEVNULL,
                                                                    stderr=asyncio.subprocess.STDOUT)
        except OSError as e:
            tell(f"couldn't start the stream program: {e}")
            return self.set("idle", f"Couldn't start the stream: {e}")
        self.emit("share", "When your friend connects, a 'share your screen' box opens (only the first time): "
                           "choose the game window. Can't see it? Press Alt+Tab, it may be behind the game.", None)
        self.set("session", f"Streaming {g['name']}. When the 'share your screen' box opens, pick the game window. "
                            "Your friend is Player 2.")
        done, _ = await asyncio.wait({asyncio.ensure_future(self.stream_proc.wait()),
                                      asyncio.ensure_future(self.proc.wait())}, return_when=asyncio.FIRST_COMPLETED)
        why = ""
        if self.stream_proc.returncode not in (None, 0):
            why = last_problem(log_path) or f"the stream stopped (code {self.stream_proc.returncode})"
            tell(why)
        if log_f:
            log_f.close()
        for p in (self.stream_proc, self.proc):
            if p and p.returncode is None:
                try:
                    p.terminate()
                except ProcessLookupError:
                    pass
        self.stream_proc = None
        if self.state == "session":
            self.set("idle", f"The stream stopped: {why}. Details: {log_path}" if why else
                     "Session ended. Press the button to find a new opponent.")


def guest_result(log_path):
    """The player's last word: why it closed (its 'GUEST ENDED:' line), or ''."""
    try:
        lines = open(log_path, errors="replace").read().splitlines()
    except OSError:
        return ""
    hit = [l for l in lines if l.startswith("GUEST ENDED:")]
    if hit:
        return hit[-1][len("GUEST ENDED:"):].strip()[:300]
    tb = [l for l in lines if l.strip()]
    return ("The player closed unexpectedly: " + tb[-1].strip()[:200]) if tb else ""


def last_problem(log_path):
    """The most telling line of the stream host's output (an error if there is one), in a short form."""
    try:
        lines = [l.strip() for l in open(log_path, errors="replace").read().splitlines() if l.strip()]
    except OSError:
        return ""
    for key in ("STREAM ERROR:", "Can't ", "No usable", "Error", "error", "Traceback"):
        hit = [l for l in lines if key in l]
        if hit:
            return hit[-1].replace("STREAM ERROR:", "").replace("[stream]", "").strip()[:200]
    return lines[-1][:200] if lines else ""


def tell_guest(host, port, key, text):
    """Tell the friend why the stream stopped, in the host program's own words: a goodbye over the stream connection
    (as the host of that session), which ends the friend's player with this reason."""
    import stream_signal
    return stream_signal.send_once(host, port, key, [{"type": "bye", "why": text[:300]}])


def open_app_link(url):
    """Hand an app link (parsec://...) to the program registered for it. Not a web browser."""
    try:
        if IS_WIN:
            os.startfile(url)                      # noqa: S606 (only parsec:// links built above)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ----------------------------------------------------------------- controller hotkey
class Pad(threading.Thread):
    """Watches game controllers: the hotkey, plus 'press a button to map it'. Needs pygame."""

    def __init__(self, get_cfg, on_fire, on_note):
        super().__init__(daemon=True)
        self.get_cfg, self.on_fire, self.on_note = get_cfg, on_fire, on_note
        self.capture_cb, self.learn_req, self.names = None, None, []

    def capture(self, cb):
        self.capture_cb = cb

    def learn(self, device, cb):
        """Call cb(token) with the next button/d-pad/trigger the player presses on controller `device`."""
        self.learn_req = {"dev": device, "cb": cb, "phase": "release", "base": []}

    def cancel_learn(self):
        self.learn_req = None

    @staticmethod
    def read(j):
        return {"buttons": {b for b in range(j.get_numbuttons()) if j.get_button(b)},
                "hats": [j.get_hat(h) for h in range(j.get_numhats())],
                "axes": [j.get_axis(a) for a in range(j.get_numaxes())]}

    def run(self):
        try:
            import pygame
        except ImportError:
            return self.on_note("Controllers can't be read (pygame is not installed), so the hotkey and button mapping are off.")
        os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
        pygame.init()
        pygame.joystick.init()
        pads, held_since, fired, best, cap_start = {}, None, False, set(), 0
        while True:
            try:
                pygame.event.pump()
                if pygame.joystick.get_count() != len(pads):
                    pads = {i: pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())}
                    self.names = [j.get_name() for j in pads.values()]
                    self.on_note(f"{len(pads)} controller(s) connected." if pads else "No controller connected.")
                states = {i: self.read(j) for i, j in pads.items()}
                sets = [st["buttons"] for st in states.values()]
                now = time.time()
                req = self.learn_req
                if req:
                    st = states.get(req["dev"])
                    if st is not None and req["phase"] == "release":
                        if controls.is_idle(st):
                            req["base"], req["phase"] = st["axes"], "press"
                    elif st is not None:
                        tok = controls.detect_input(st, req["base"])
                        if tok:
                            self.learn_req = None
                            req["cb"](tok)
                    held_since = None
                elif self.capture_cb:
                    cur = max(sets, key=len, default=set())
                    cap_start = cap_start or now
                    if len(cur) > len(best):
                        best = cur
                    if (best and not cur) or now - cap_start > 8:
                        cb, self.capture_cb = self.capture_cb, None
                        cb(sorted(best) or None)
                        best, cap_start = set(), 0
                    held_since = None
                else:
                    cfg = self.get_cfg()
                    combo = set(cfg["hotkey"])
                    down = any(combo <= x for x in sets)
                    if down and held_since is None:
                        held_since, fired = now, False
                    elif not down:
                        held_since = None
                    if held_since and not fired and now - held_since >= cfg["hold"]:
                        fired = True
                        self.on_fire()
            except Exception:
                pass
            time.sleep(0.02)


# ----------------------------------------------------------------- the window
def start_local_server(port, note):
    import server as mm
    _local_mm.update(ok=False, err="", id="")

    def ready():
        _local_mm.update(ok=True, id=mm.INSTANCE)

    def go():
        try:
            asyncio.run(mm.main(port, ready=ready))
        except Exception as e:                 # e.g. the port is taken by another program
            _local_mm.update(ok=False, err=str(e))
            note(f"Couldn't start the matchmaker on this PC: {e}")
    threading.Thread(target=go, daemon=True).start()


def choose_matchmaker(cfg, found=None):
    """The matchmaker this computer should use on the home network, as (host, port, name), or None.
    Same rule as every other device (lan.pick), so phones and computers end up together without typing anything.
    This computer's own matchmaker is used as 127.0.0.1."""
    found = lan.find_all(int(cfg.get("server_port", 7777))) if found is None else found
    best = lan.pick(found)
    if not best:
        return None
    if best.get("id") and best["id"] == _local_mm.get("id"):
        return "127.0.0.1", best["port"], "this computer"
    return best["host"], best["port"], best["name"] or best["host"]


def auto_matchmaker_on(cfg):
    """Pick the matchmaker by itself unless the player typed an internet address on purpose."""
    host = str(cfg.get("server_host", "")).strip()
    return cfg.get("auto_mm", True) and (lan.is_loopback(host) or lan.is_private(host))


def run_gui():
    if instance.hand_over(sys.argv[1:]):
        return            # the app is already open: it got the link (or just came to the front)
    runtime.restore_child_env()
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    cfg = load_cfg()
    first_run = not CONFIG_PATH.exists()
    autodetect(cfg)
    save_cfg(cfg)

    root = tk.Tk(className="Retro-Netplay")
    root.title("Retro-Netplay")
    # fit small laptop screens too (the window scrolls nothing, so keep it within the screen)
    tall = max(680, min(900, root.winfo_screenheight() - 90))
    root.geometry(f"580x{tall}")
    root.minsize(560, min(tall, 760))
    fam = ink.apply_theme(root)
    try:
        root.iconphoto(True, tk.PhotoImage(file=str(HERE / "icon-256.png")))
    except tk.TclError:
        pass
    msgs = queue.Queue()
    import toast
    toasts = toast.Toasts(root, tk, fam, lambda: cfg.get("toast_corner", "top-right"),
                          lambda c: (cfg.__setitem__("toast_corner", c), save_cfg(cfg)))
    engine = Engine(lambda: cfg, lambda state, msg, clip=None: msgs.put((state, msg, clip)))
    # the controller hotkey answers an open approval card first (Accept), otherwise it joins/leaves the queue
    pad = Pad(lambda: cfg, lambda: msgs.put(("call", None, lambda: toasts.hotkey() or engine.hotkey())),
              lambda t: msgs.put(("note", t, None)))
    pad.start()
    mm_note = lambda t: msgs.put(("note", t, None))
    engine.on_boot = lambda c: ensure_local_server(c, mm_note)
    ensure_local_server(cfg, mm_note)

    # ---- hero: ink controller with four live heat squares (emulator, game, online, controllers)
    HW, HH = 580, (250 if tall >= 860 else 180)
    hero = tk.Canvas(root, width=HW, height=HH, bg=ink.PAPER, highlightthickness=0)
    hero.pack(fill="x")
    ink.draw_hero(hero, HW, HH, fam)
    # squares sit clear of the corner captions (top-left and bottom-right)
    spots = ([(0.14, 0.40, 2, "Emulator"), (0.86, 0.26, 2, "Game"), (0.72, 0.66, 3, "Online"), (0.16, 0.80, 2, "Controllers")]
             if HH >= 220 else
             [(0.30, 0.26, 2, "Emulator"), (0.86, 0.24, 2, "Game"), (0.66, 0.58, 2, "Online"), (0.10, 0.74, 2, "Controllers")])
    heats = [ink.Heat(tk, zoom=z, seed=i + 1) for i, (_x, _y, z, _n) in enumerate(spots)]
    sq_state = {"states": [ink.UNKNOWN] * 4, "notes": [""] * 4, "clock": 0.0, "searching": False}

    def draw_squares():
        hero.delete("sq")
        for i, (fx, fy, z, name) in enumerate(spots):
            img = heats[i].paint(sq_state["states"][i], sq_state["clock"], i, sq_state["searching"])
            x, y = fx * HW, fy * HH
            hero.create_line(x, y, HW * 0.52, HH * 0.5, fill="#f4f3ef", tags="sq")
        for i, (fx, fy, z, name) in enumerate(spots):
            x, y = fx * HW, fy * HH
            hero.create_image(x, y, image=heats[i].image, tags="sq")
            half = heats[i].N * z / 2
            note = sq_state["notes"][i]
            tw = max(len(name), len(note)) * 8 + 12
            left = x + half + 4 if fx < 0.5 else x - half - 4 - tw
            hero.create_rectangle(left, y - half, left + tw, y - half + 38, fill=ink.PAPER, outline="", tags="sq")
            hero.create_text(left + 6, y - half + 4, anchor="nw", text=name.upper(), font=(fam, 9, "bold"), fill=ink.INK, tags="sq")
            hero.create_text(left + 6, y - half + 21, anchor="nw", text=note, font=(fam, 9), fill=ink.SOFT, tags="sq")

    def tick():
        if sq_state["searching"]:
            sq_state["clock"] += 0.09
            draw_squares()
        root.after(90, tick)

    frame = ttk.Frame(root, padding=(18, 12, 18, 14))
    frame.pack(fill="both", expand=True)

    row = ttk.Frame(frame)
    row.pack(fill="x")
    games_lbl = ttk.Label(row, text="YOUR GAMES", font=(fam, 9, "bold"))
    games_lbl.pack(side="left", anchor="s")
    ttk.Button(row, text="Add one...", command=lambda: add_game()).pack(side="right")
    ttk.Button(row, text="Game folders...", command=lambda: open_wizard(only=0)).pack(side="right", padx=(0, 6))
    lb_row = ttk.Frame(frame)
    lb_row.pack(fill="x", pady=(6, 10))
    games_lb = tk.Listbox(lb_row, height=(6 if tall >= 860 else 4), font=(fam, 11), activestyle="none", exportselection=False,
                          bg="#efeeea", fg=ink.INK, relief="solid", borderwidth=1, highlightthickness=2,
                          highlightcolor=ink.INK, highlightbackground=ink.PAPER,
                          selectbackground=ink.INK, selectforeground=ink.PAPER)
    games_sb = ttk.Scrollbar(lb_row, orient="vertical", command=games_lb.yview)
    games_lb.config(yscrollcommand=games_sb.set)
    games_lb.pack(side="left", fill="x", expand=True)
    games_sb.pack(side="left", fill="y")

    go_btn = ink.InkButton(tk, frame, "Play", "play", lambda: engine.play_game(), fam)
    go_btn.pack(fill="x")
    status = ttk.Label(frame, text="Ready.", wraplength=530, justify="left")
    status.pack(anchor="w", pady=(10, 4))
    link_row = ttk.Frame(frame)      # packed under the status line only while an invite link exists
    link_var = tk.StringVar()
    ttk.Entry(link_row, textvariable=link_var, state="readonly").pack(side="left", fill="x", expand=True)
    ttk.Button(link_row, text="Copy link", command=lambda: copy_link()).pack(side="left", padx=(6, 0))
    ttk.Button(link_row, text="Post now", command=lambda: engine.announce_now()).pack(side="left", padx=(6, 0))
    hint = ttk.Label(frame, text="", foreground=ink.SOFT, wraplength=530, justify="left")
    hint.pack(anchor="w", pady=(0, 8))

    ttk.Label(frame, text="SETUP CHECK", font=(fam, 9, "bold")).pack(anchor="w")
    checks = ttk.Frame(frame)
    checks.pack(fill="x", pady=(4, 8))

    hot_row = ttk.Frame(frame)
    hot_row.pack(fill="x", side="bottom", pady=(8, 0))
    bottom = ttk.Frame(frame)
    bottom.pack(fill="x", side="bottom")
    bottom2 = ttk.Frame(frame)
    bottom2.pack(fill="x", side="bottom", pady=(0, 6))
    ttk.Button(bottom2, text="Friends...", command=lambda: open_friends()).pack(side="left")
    ttk.Button(bottom2, text="Invites & posting...", command=lambda: open_invites()).pack(side="left", padx=(6, 0))
    ttk.Button(bottom2, text="Enter code...", command=lambda: join_with_link()).pack(side="left", padx=(6, 0))
    ttk.Button(bottom, text="Settings...", command=lambda: open_settings()).pack(side="left")
    ttk.Button(bottom, text="Controllers...", command=lambda: open_controllers()).pack(side="left", padx=(6, 0))
    ttk.Button(bottom, text="Set controller hotkey", command=lambda: set_hotkey()).pack(side="left", padx=(6, 0))
    hot_lbl = ttk.Label(hot_row, text="", foreground=ink.SOFT, wraplength=530, justify="left")
    hot_lbl.pack(side="left")

    def current():
        gs = cfg["games"]
        return gs[min(cfg["last_game"], len(gs) - 1)] if gs else None

    def refresh():
        top = games_lb.yview()[0]
        games_lb.delete(0, "end")
        for g in cfg["games"]:
            games_lb.insert("end", f"  {g['name']}   ·   {SYSTEMS[g['system']]['label']}")
        n = len(cfg["games"])
        games_lbl.config(text=f"YOUR GAMES  ·  {n}" if n else "YOUR GAMES")
        if n:
            cfg["last_game"] = max(0, min(cfg["last_game"], n - 1))
            games_lb.selection_clear(0, "end")
            games_lb.selection_set(cfg["last_game"])
            games_lb.yview_moveto(top)
            games_lb.see(cfg["last_game"])
        else:
            games_lb.insert("end", "  No games yet: click Game folders... and pick where your games are.")
        for w in checks.winfo_children():
            w.destroy()
        g = current()
        if g is None:
            ttk.Label(checks, text="Click Game folders... and pick the folders where your games are.").pack(anchor="w")
        else:
            for ok, text in checklist(cfg, g, len(pad.names)):
                mark, tone = {True: ("OK", 0.30), False: ("FIX", 0.86), None: ("TIP", 0.62)}[ok]
                r = ttk.Frame(checks)
                r.pack(fill="x", anchor="w", pady=1)
                tk.Label(r, text=mark, bg=ink.spectrum(tone), fg=ink.INK, width=4, font=(fam, 9, "bold")).pack(side="left", padx=(0, 8))
                ttk.Label(r, text=text, wraplength=470, justify="left").pack(side="left")
            hint.config(text="Double-click a game (or select it and press Play) to start it and join the queue.")
        update_squares(g)
        combo_name = "Start + Select" if sorted(cfg["hotkey"]) == [6, 7] else "buttons " + " + ".join(str(b) for b in cfg["hotkey"])
        hot_lbl.config(text=f"In a game: hold {combo_name} for {cfg['hold']:g} s to join or leave the queue")
        save_cfg(cfg)

    def update_squares(g):
        st, nt = [ink.UNKNOWN] * 4, [""] * 4
        if g is not None:
            sysd = SYSTEMS[g["system"]]
            exe = cfg["paths"].get(sysd["emu"], "")
            if emu_ok(exe):
                st[0], nt[0] = ink.READY, "found"
            else:
                st[0], nt[0] = ink.ATTENTION, "not found"
            have = Path(g["rom"]).exists()
            st[1], nt[1] = (ink.READY, "file found") if have else (ink.ATTENTION, "no file")
        else:
            st[1], nt[1] = ink.ATTENTION, "add one"
        online = bool(cfg.get("run_server")) or str(cfg.get("server_host", "")).strip() not in ("", "127.0.0.1", "localhost")
        st[2], nt[2] = (ink.READY, "set") if online else (ink.ATTENTION, "not set")
        n = len(pad.names)
        st[3], nt[3] = (ink.READY, "%d ready" % n) if n >= 2 else ((ink.UNKNOWN, "1 found") if n == 1 else (ink.ATTENTION, "none"))
        sq_state["states"], sq_state["notes"] = st, nt
        draw_squares()

    def on_pick(_e=None):
        sel = games_lb.curselection()
        if sel and sel[0] < len(cfg["games"]) and sel[0] != cfg["last_game"]:
            cfg["last_game"] = sel[0]
            refresh()
    games_lb.bind("<<ListboxSelect>>", on_pick)

    def play_selected(_e=None):
        on_pick()
        if cfg["games"] and engine.state == "idle":
            engine.play_game()
    games_lb.bind("<Double-Button-1>", play_selected)
    games_lb.bind("<Return>", play_selected)

    def ask_system(exts_name, options):
        win = tk.Toplevel(root)
        win.title("Which system?")
        win.transient(root)
        win.grab_set()
        ttk.Label(win, text=f"What system is {exts_name} for?", padding=10).pack()
        var = tk.StringVar(value=options[0])
        for key in options:
            ttk.Radiobutton(win, text=SYSTEMS[key]["label"], variable=var, value=key).pack(anchor="w", padx=16)
        out = {}
        def ok():
            out["v"] = var.get()
            win.destroy()
        ttk.Button(win, text="OK", command=ok).pack(pady=10)
        root.wait_window(win)
        return out.get("v")

    def add_game():
        path = filedialog.askopenfilename(title="Choose your game file")
        if not path:
            return
        ext = Path(path).suffix.lower()
        options = [k for k, s in SYSTEMS.items() if ext in s["exts"]] or list(SYSTEMS)
        system = options[0] if len(options) == 1 else ask_system(Path(path).name, options)
        if not system:
            return
        default = re.sub(r"\s*[\(\[].*?[\)\]]", "", Path(path).stem).replace("_", " ").strip()
        name = simpledialog.askstring(
            "Game name",
            "Name this game.\n\nYour opponent has to type the exact same name\nto be matched with you.",
            initialvalue=default, parent=root)
        if not name or not name.strip():
            return
        cfg["games"].append({"name": name.strip(), "system": system, "rom": path})
        cfg["last_game"] = len(cfg["games"]) - 1
        refresh()

    def remove_game():
        if cfg["games"] and messagebox.askyesno("Remove game", "Remove this game from the list?\n(Your game file is not deleted.)"):
            del cfg["games"][cfg["last_game"]]
            cfg["last_game"] = 0
            refresh()

    def set_hotkey():
        status.config(text="Hold the controller buttons you want to use together, then let go.")
        def done(buttons):
            if buttons:
                cfg["hotkey"] = buttons
            msgs.put(("hotkey", "Hotkey saved." if buttons else "No buttons detected. Hotkey unchanged.", None))
        pad.capture(done)

    def open_settings():
        win = tk.Toplevel(root)
        win.title("Settings")
        win.transient(root)
        win.grab_set()
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        vars_ = {}

        def field(label, key, store, browse=False, folder=False):
            r = ttk.Frame(body)
            r.pack(fill="x", pady=3)
            ttk.Label(r, text=label, width=22).pack(side="left")
            v = tk.StringVar(value=str(store.get(key, "")))
            vars_[(id(store), key)] = (store, key, v)
            ttk.Entry(r, textvariable=v, width=34).pack(side="left", fill="x", expand=True)
            if browse:
                def pick():
                    p = filedialog.askdirectory() if folder else filedialog.askopenfilename()
                    if p:
                        v.set(p)
                ttk.Button(r, text="Browse", command=pick).pack(side="left", padx=(4, 0))

        ttk.Label(body, text="Emulators", font=("", 10, "bold")).pack(anchor="w")
        for key, (label, _, _) in EMUS.items():
            field(label, key, cfg["paths"], browse=True)
        field("RetroArch cores", "cores", cfg["paths"], browse=True, folder=True)
        ttk.Label(body, text="(cores folder is only needed if the check says a core is missing)",
                  foreground="#666").pack(anchor="w")
        ttk.Label(body, text="Online", font=("", 10, "bold")).pack(anchor="w", pady=(10, 0))
        be = ttk.Frame(body)
        be.pack(fill="x", pady=3)
        ttk.Label(be, text="Streaming (PS2/PS3)", width=22).pack(side="left")
        be_var = tk.StringVar(value="Built-in (recommended)" if cfg.get("stream_backend", "own") == "own" else "Parsec")
        ttk.Combobox(be, textvariable=be_var, values=["Built-in (recommended)", "Parsec"], state="readonly", width=31).pack(side="left")
        relay_var = tk.BooleanVar(value=cfg.get("use_relay", True))
        ttk.Checkbutton(body, text="Use the matchmaker's relay server when a direct stream connection fails\n"
                                    "(only if whoever runs the matchmaker set one up; the stream stays encrypted)",
                        variable=relay_var).pack(anchor="w", pady=(0, 4))
        field("Your Parsec peer ID", "parsec_peer_id", cfg)
        ttk.Label(body, text="(Parsec ID is only used when Streaming is set to Parsec)", foreground="#666").pack(anchor="w")
        field("Matchmaker address", "server_host", cfg)
        field("Matchmaker port", "server_port", cfg)
        run_var = tk.BooleanVar(value=cfg["run_server"])
        ttk.Checkbutton(body, text="Run the matchmaker on this PC (only ONE player does this; other devices\n"
                                    "find it with \"Find on my network\")", variable=run_var).pack(anchor="w", pady=6)
        lan_row = ttk.Frame(body)
        lan_row.pack(fill="x")
        lan_note = ttk.Label(body, text=f"This PC on your home network: {lan.lan_ip() or 'unknown'}.  " + lan.FIREWALL_HELP,
                             wraplength=560, justify="left", foreground=ink.SOFT)

        def find_mm():
            lan_note.config(text="Looking on your home network...")
            def work():
                c = choose_matchmaker(cfg)
                def show():
                    if not win.winfo_exists():
                        return
                    if not c:
                        lan_note.config(text="No Retro-Netplay found on this network. " + lan.FIREWALL_HELP)
                        return
                    host, port, name = c
                    vars_[(id(cfg), "server_host")][2].set(host)
                    vars_[(id(cfg), "server_port")][2].set(str(port))
                    lan_note.config(text=f"Found it: {name}" + ("" if host == "127.0.0.1" else f" ({host})") + ". Press Save.")
                win.after(0, show)
            threading.Thread(target=work, daemon=True).start()
        ttk.Button(lan_row, text="Find on my network", command=find_mm).pack(side="left")
        ttk.Button(lan_row, text="My address / connect by address...", command=open_address).pack(side="left", padx=(6, 0))
        lan_note.pack(anchor="w", pady=(4, 0))

        def save():
            for store, key, v in vars_.values():
                store[key] = v.get().strip()
            cfg["run_server"] = run_var.get()
            h = str(cfg.get("server_host", "")).strip()
            cfg["auto_mm"] = lan.is_loopback(h) or lan.is_private(h)    # an internet address typed on purpose stays put
            cfg["stream_backend"] = "own" if be_var.get().startswith("Built-in") else "parsec"
            cfg["use_relay"] = relay_var.get()
            try:
                cfg["server_port"] = int(cfg["server_port"])
            except ValueError:
                cfg["server_port"] = 7777
            if cfg["run_server"]:
                cfg["server_host"] = cfg["server_host"] or "127.0.0.1"
                ensure_local_server(cfg, lambda t: msgs.put(("note", t, None)))
            win.destroy()
            refresh()

        btns = ttk.Frame(body)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Auto-detect emulators", command=lambda: (autodetect(cfg), win.destroy(), refresh(), open_settings())).pack(side="left")
        ttk.Button(btns, text="Run setup again", command=lambda: (win.destroy(), open_wizard())).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Streaming check", command=lambda: show_stream_check()).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Save", command=save).pack(side="right")

    def over_modal(win):
        """A window opened from a window that holds all clicks (Settings): take the clicks while open, give them back after.
        Without this its buttons do nothing."""
        prev = root.grab_current()
        if not prev:
            return
        win.grab_set()
        def back(_e=None, prev=prev):
            try:
                if prev.winfo_exists():
                    prev.grab_set()
            except tk.TclError:
                pass
        win.bind("<Destroy>", lambda e: back() if e.widget is win else None)

    def show_stream_check():
        win = tk.Toplevel(root)
        win.title("Streaming check")
        win.transient(root)
        over_modal(win)
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, wraplength=560, justify="left", text=(
            "Hosting a PS2/PS3 stream for a friend uses GStreamer from your computer. Lines marked [X] need the "
            "package shown; copy the install command into a terminal. Joining a friend's stream uses the player "
            "window, checked at the bottom.")
                  ).pack(anchor="w")
        txt = tk.Text(body, width=86, height=16, font=(fam, 10), wrap="word", bg="#efeeea", fg=ink.INK, relief="solid", borderwidth=1)
        txt.pack(fill="both", expand=True, pady=(8, 8))
        txt.insert("end", "Checking...")
        def work():
            ok, report = stream_doctor()
            own_host_ready(refresh=True)
            gok, greport = guest_ready(refresh=True)
            text = (("All set: you can host.\n\n" if ok else "") + report + "\n\nJOINING A FRIEND'S STREAM (the player window)\n"
                    + ("All set: you can join.\n" if gok else "") + greport)
            msgs.put(("call", None, lambda: (txt.delete("1.0", "end"), txt.insert("end", text), refresh(), resend_status())))
        threading.Thread(target=work, daemon=True).start()
        ttk.Button(body, text="Close", command=win.destroy).pack(side="right")

        def test_share():
            """Open the desktop's 'share your screen' box once, without a friend, and say whether it worked."""
            txt.delete("1.0", "end")
            txt.insert("end", "A 'share your screen' box should open now (it may be behind other windows: Alt+Tab). "
                              "Choose the game window...")
            def show_line(line):
                if win.winfo_exists():
                    txt.insert("end", "\n" + line)
                    txt.see("end")
            def work2():
                # each step shows as it happens, so it's clear where it stops (and what to send if it does)
                try:
                    pr = subprocess.Popen([runtime.host_python(), "-u", str(runtime.host_script()), "--test-share"],
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    for line in pr.stdout:
                        if line.strip():
                            msgs.put(("call", None, lambda l=line.rstrip(): show_line(l)))
                    pr.wait()
                except (OSError, TypeError) as e:
                    msgs.put(("call", None, lambda: show_line(f"Problem: {e}")))
            threading.Thread(target=work2, daemon=True).start()
        ttk.Button(body, text="Test screen sharing", command=test_share).pack(side="left")

    def show_manual():
        win = tk.Toplevel(root)
        win.title("Controller setup manual")
        txt = tk.Text(win, width=70, height=26, wrap="word", padx=10, pady=10)
        txt.insert("1.0", MANUAL)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)
        bar = ttk.Frame(win, padding=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="Open Controllers...", command=lambda: (win.destroy(), open_controllers())).pack(side="left")
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")

    def controller_check(then=None):
        win = tk.Toplevel(root)
        win.title("Quick controller check")
        win.transient(root)
        win.grab_set()
        box = ttk.Frame(win, padding=16)
        box.pack()
        ttk.Label(box, text="Is your controller setup ready for 2 players?", font=("", 12, "bold")).pack(anchor="w")
        ttk.Label(box, wraplength=420, justify="left", text=(
            "Most games here are better with two players. Player 1 and Player 2 are always on, so a "
            "second person can jump in at any time. Both controllers should be connected before you start.")).pack(anchor="w", pady=8)
        live = ttk.Label(box, font=("", 10, "bold"))
        live.pack(anchor="w", pady=(0, 10))

        def tick():
            if not win.winfo_exists():
                return
            n = len(pad.names)
            live.config(text=f"Controllers detected right now: {n}" + ("  (two found, you're set!)" if n >= 2 else ""))
            win.after(500, tick)
        tick()

        def close(manual=False):
            cfg["controller_check_done"] = True
            save_cfg(cfg)
            win.destroy()
            if manual:
                show_manual()
            if then:
                then()
        ttk.Button(box, text="Yes, it's ready", command=close).pack(fill="x", pady=2)
        ttk.Button(box, text="No, show me how to set it up", command=lambda: close(True)).pack(fill="x", pady=2)
        ttk.Button(box, text="Skip for now", command=close).pack(fill="x", pady=2)

    def map_slot(i, after):
        dev = controls.device_for(cfg["slots"], i)
        if dev >= len(pad.names):
            messagebox.showinfo("No controller", f"Player {i + 1} has no controller connected yet.\n"
                                "Plug it in or turn it on, then try again.")
            return
        win = tk.Toplevel(root)
        win.title(f"Map Player {i + 1}")
        win.transient(root)
        win.grab_set()
        prompt = tk.StringVar()
        ttk.Label(win, textvariable=prompt, wraplength=380, justify="left", padding=16, font=("", 11)).pack()
        new, idx = {}, [0]

        def ask():
            if idx[0] >= len(controls.ACTIONS):
                cfg["slots"][i]["binds"] = new or None
                win.destroy()
                save_cfg(cfg)
                return after()
            _, label = controls.ACTIONS[idx[0]]
            prompt.set(f"Player {i + 1}: press {label} on controller {dev + 1}.   ({idx[0] + 1} of {len(controls.ACTIONS)})")
            pad.learn(dev, lambda tok: msgs.put(("call", "", lambda: got(tok))))

        def got(tok):
            if win.winfo_exists():
                new[controls.ACTIONS[idx[0]][0]] = tok
                idx[0] += 1
                ask()

        def skip():
            pad.cancel_learn()
            idx[0] += 1
            ask()

        def cancel():
            pad.cancel_learn()
            win.destroy()
        bar = ttk.Frame(win, padding=(16, 0, 16, 16))
        bar.pack(fill="x")
        ttk.Button(bar, text="Skip this button", command=skip).pack(side="left")
        ttk.Button(bar, text="Cancel", command=cancel).pack(side="right")
        ask()

    def open_controllers():
        win = tk.Toplevel(root)
        win.title("Controllers")
        win.transient(root)
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Players", font=("", 11, "bold")).pack(anchor="w")
        ttk.Label(body, wraplength=520, justify="left", text=(
            "Player 1 and Player 2 are always on, so someone can pick up a controller and join at any time. "
            "Tick Player 3 or 4 for bigger groups. Map one controller, then Copy and Paste its layout to the rest.")).pack(anchor="w", pady=(0, 8))
        grid = ttk.Frame(body)
        grid.pack(fill="x")
        clip = {"i": None}

        def draw():
            for w in grid.winfo_children():
                w.destroy()
            slots = cfg["slots"]
            devs = ["Auto"] + [f"Controller {k + 1}: {n}" for k, n in enumerate(pad.names)]
            for i, sl in enumerate(slots):
                r = ttk.Frame(grid)
                r.pack(fill="x", pady=3)
                if i < controls.ALWAYS_ON:
                    ttk.Label(r, text=f"Player {i + 1}  (always on)", width=22).pack(side="left")
                else:
                    v = tk.BooleanVar(value=sl["enabled"])
                    ttk.Checkbutton(r, text=f"Player {i + 1}", variable=v, width=20,
                                    command=lambda i=i, v=v: (controls.set_enabled(cfg["slots"], i, v.get()), save_cfg(cfg), draw())).pack(side="left")
                cb = ttk.Combobox(r, values=devs, state="readonly", width=26)
                cb.current(0 if sl["device"] is None else min(sl["device"] + 1, len(devs) - 1))
                cb.pack(side="left")
                cb.bind("<<ComboboxSelected>>", lambda e, i=i, cb=cb: (cfg["slots"][i].update(device=None if cb.current() == 0 else cb.current() - 1), save_cfg(cfg), draw()))
                on = sl["enabled"]
                ttk.Button(r, text="Map...", command=lambda i=i: map_slot(i, draw)).pack(side="left", padx=(6, 0))
                ttk.Button(r, text="Copy", command=lambda i=i: (clip.update(i=i), draw())).pack(side="left", padx=(4, 0))
                ttk.Button(r, text="Paste", state=("normal" if clip["i"] is not None and clip["i"] != i and on else "disabled"),
                           command=lambda i=i: (controls.copy_binds(cfg["slots"], clip["i"], i), save_cfg(cfg), draw())).pack(side="left", padx=(4, 0))
                if on:
                    here = controls.ready(slots, i, len(pad.names))
                    tk.Label(r, text="ready" if here else "waiting...", fg="#1a8a3a" if here else "#b26a00", width=11, anchor="w").pack(side="left", padx=(6, 0))
                    if sl["binds"]:
                        ttk.Label(r, text="custom").pack(side="left")
            note = "Copied Player %d's layout. Click Paste on the player to give it to." % (clip["i"] + 1) if clip["i"] is not None else \
                "Layouts that aren't mapped use your emulator's automatic controller detection."
            ttk.Label(grid, text=note, foreground="#666", wraplength=520, justify="left").pack(anchor="w", pady=(8, 0))
        draw()
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="Copy Player 1 to everyone", command=lambda: (controls.copy_to_all(cfg["slots"], 0), save_cfg(cfg), draw())).pack(side="left")
        ttk.Button(bar, text="Reset all layouts", command=lambda: ([sl.update(binds=None) for sl in cfg["slots"]], save_cfg(cfg), draw())).pack(side="left", padx=6)
        ttk.Button(bar, text="Setup manual", command=show_manual).pack(side="right")
        seen = [len(pad.names)]

        def watch():  # redraw only when a controller is plugged in or removed
            if win.winfo_exists():
                if len(pad.names) != seen[0]:
                    seen[0] = len(pad.names)
                    draw()
                win.after(700, watch)
        watch()

    def copy_link():
        if link_var.get():
            root.clipboard_clear()
            root.clipboard_append(link_var.get())
            status.config(text="Invite link copied. Paste it anywhere to invite people.")

    def join_with_link(text=None):
        text = text or simpledialog.askstring("Enter code", "Type the invite code your friend gave you (like ABCD-2345) "
                                              "or a friend code (like K7M-4QZ).\nYou can also paste an invite link or a "
                                              "friend's address (like 203.0.113.5:7777).", parent=root)
        if not text:
            return
        code = invites.typed_code(text)
        if code:
            return join_code(code)
        f = social.parse_friend(text)
        if f and "/join" not in text:
            return add_friend_from(f)
        parsed = invites.parse_link(text)
        if not parsed:
            addr = lan.parse_address(text)
            if addr:
                return connect_to(*addr)
            messagebox.showinfo("Link", "That doesn't look like a Retro-Netplay invite, friend link, friend code or address.")
            return
        engine.join(parsed)

    def join_code(code):
        """An invite code typed in: ask this computer's matchmaker which game it is, then join like a link would."""
        host, port = cfg["server_host"], int(cfg["server_port"])
        status.config(text=f"Looking up the code {invites.pretty_code(code)}...")
        def work():
            game = invites.lookup_code(host, port, code)
            def done():
                if game is None:
                    status.config(text="Couldn't reach the matchmaker to look up that code. " + (no_matchmaker_hint(cfg) or
                                       "Check your connection and try again."))
                elif not game:
                    status.config(text=f"No game is waiting under the code {invites.pretty_code(code)} right now. Check the "
                                       "code, and that your friend is still looking for a match (and on the same matchmaker).")
                else:
                    engine.join({"host": host, "port": port, "game": game, "code": code})
            msgs.put(("call", None, done))
        threading.Thread(target=work, daemon=True).start()

    def connect_to(host, port):
        """Use a friend's matchmaker by its address (what they copied from 'Your address')."""
        cfg["server_host"], cfg["server_port"] = host, int(port)
        cfg["auto_mm"] = lan.is_private(host)        # a home-network address keeps healing itself; an internet one stays
        save_cfg(cfg)
        restart_presence()
        refresh()
        status.config(text=f"Connected to the matchmaker at {host}:{port}. Add each other with friend codes, then Join.")

    def open_address():
        win = tk.Toplevel(root)
        win.title("Your address")
        win.transient(root)
        over_modal(win)
        body = ttk.Frame(win, padding=16)
        body.pack(fill="both", expand=True)
        port = int(cfg["server_port"])
        hosting = lan.is_loopback(cfg.get("server_host"))

        def line(caption, value, note=""):
            ttk.Label(body, text=caption.upper(), font=(fam, 9, "bold")).pack(anchor="w", pady=(12, 0))
            row = ttk.Frame(body)
            row.pack(fill="x", pady=(2, 0))
            lbl = ttk.Label(row, text=value or "unknown", font=(fam, 16, "bold"))
            lbl.pack(side="left")
            def copy():
                v = lbl.cget("text")
                if v and v != "unknown" and not v.startswith("asking"):
                    root.clipboard_clear()
                    root.clipboard_append(lan.share_text(*v.rsplit(":", 1)) if ":" in v else v)
                    status.config(text="Copied. Send it to your friend; they paste it into Enter code... (phone: Settings > My address / connect by address).")
            ttk.Button(row, text="Copy", command=copy).pack(side="right")
            if note:
                ttk.Label(body, text=note, foreground=ink.SOFT, wraplength=520, justify="left").pack(anchor="w")
            return lbl

        if hosting:
            ttk.Label(body, wraplength=520, justify="left", text=(
                "This computer runs the matchmaker. Give friends one of these addresses; they paste it in their app "
                "(Enter code... on a computer; Settings > My address / connect by address on a phone).")).pack(anchor="w")
            home = lan.lan_ip()
            line("Friends on your Wi-Fi", f"{home}:{port}" if home else "", "Same home network only.")
            pub = line("Friends anywhere else", "asking your router...",
                       f"Works only if your router lets ports {port} and {port + 1} in (UPnP or port forwarding).")
            def look_up(online=False):
                pub.config(text="asking online..." if online else "asking your router...")
                def work():
                    ip = lan.public_ip_online() if online else lan.public_ip_from_router()
                    msgs.put(("call", None, lambda: pub.config(text=f"{ip}:{port}" if ip else "unknown") if pub.winfo_exists() else None))
                threading.Thread(target=work, daemon=True).start()
            ttk.Button(body, text="Router didn't say? Look it up online", command=lambda: look_up(True)).pack(anchor="w", pady=(6, 0))
            ttk.Label(body, text=f"(That asks {lan.LOOKUP_URL}, which sees your address. Only when you press it.)",
                      foreground=ink.SOFT).pack(anchor="w")
            look_up(False)
        else:
            host = str(cfg.get("server_host", "")).strip()
            ttk.Label(body, wraplength=520, justify="left", text=(
                "You use a friend's matchmaker. Everyone who plays together uses the same one, so this is the address "
                "to pass on.")).pack(anchor="w")
            line("Matchmaker", f"{host}:{port}")
            me = line("Your own address (what the matchmaker sees)", "asking...")
            def work():
                ip = lan.public_ip_from_matchmaker(host, port) or lan.lan_ip()
                msgs.put(("call", None, lambda: me.config(text=ip or "unknown") if me.winfo_exists() else None))
            threading.Thread(target=work, daemon=True).start()

        ttk.Label(body, text="GOT AN ADDRESS FROM A FRIEND?", font=(fam, 9, "bold")).pack(anchor="w", pady=(18, 0))
        row = ttk.Frame(body)
        row.pack(fill="x", pady=(4, 0))
        v = tk.StringVar()
        ttk.Entry(row, textvariable=v, width=30).pack(side="left", fill="x", expand=True)
        def go():
            addr = lan.parse_address(v.get())
            if not addr:
                return status.config(text="That isn't an address. It looks like 192.168.1.23 or 203.0.113.5:7777.")
            win.destroy()
            connect_to(*addr)
        ttk.Button(row, text="Connect", command=go).pack(side="left", padx=(6, 0))

    # ---- friends: your code, add a friend by code, who's online, and Join (your friend allows it and hosts)
    social_state = {"p": None, "online": {}, "redraw": None, "up": False, "want": None, "why": ""}

    def restart_presence():
        """Connected whenever a matchmaker is set, so friends see you and requests and joins reach you."""
        old = social_state["p"]
        if old:
            old.stop()
        social_state["p"], social_state["online"], social_state["up"], social_state["why"] = None, {}, False, ""
        host = str(cfg.get("server_host", "")).strip()
        if host and not no_matchmaker_hint(cfg):
            p = social.Presence(host, int(cfg["server_port"]), cfg["secret"], cfg["my_name"], social.friend_ids(cfg),
                                lambda ev, data: msgs.put(("social", ev, data)))
            social_state["p"] = p
            p.start()
        redraw_friends()

    def redraw_friends():
        if social_state["redraw"]:
            social_state["redraw"]()

    def auto_find(force=False):
        """Find this home network's matchmaker and use it: at start, and whenever the connection drops.
        Nobody has to type or pass on an address; every device picks the same matchmaker by the same rule."""
        if not auto_matchmaker_on(cfg) or social_state.get("finding"):
            return
        now = time.monotonic()
        if not force and now - social_state.get("found_at", -1e9) < 20:
            return
        social_state["finding"], social_state["found_at"] = True, now
        redraw_friends()

        def work():
            try:
                c = choose_matchmaker(cfg)
            except Exception:
                c = None

            def use():
                social_state["finding"], social_state["nothing_found"] = False, c is None
                if c is None and _local_mm.get("ok"):
                    c2 = ("127.0.0.1", int(cfg["server_port"]), "this computer")   # only this one: use it
                else:
                    c2 = c
                if c2:
                    host, port, name = c2
                    cur = (str(cfg.get("server_host", "")).strip(), int(cfg["server_port"]))
                    cfg["server_name"] = name
                    if (host, port) != cur:
                        cfg["server_host"], cfg["server_port"] = host, port
                        save_cfg(cfg)
                        restart_presence()
                        refresh()
                        if host != "127.0.0.1":
                            hint.config(text=f"Using the matchmaker on {name}, found on your Wi-Fi.")
                    elif not social_state["up"] and social_state["p"] is None:
                        restart_presence()
                redraw_friends()
            msgs.put(("call", None, use))
        threading.Thread(target=work, daemon=True).start()

    def keep_finding():
        if not social_state["up"]:
            auto_find()
        elif social_state.get("state", "idle") != "idle":
            recheck_streaming()
        root.after(30000, keep_finding)

    def friends_changed():
        """The list changed: save it and tell the matchmaker on the open connection (no reconnect)."""
        save_cfg(cfg)
        if social_state["p"]:
            social_state["p"].set_friends(social.friend_ids(cfg))
        redraw_friends()

    def presence_status(state):
        social_state["state"] = state
        p = social_state["p"]
        if not p:
            return
        recheck_streaming()
        g = engine.ingame["game"] if engine.ingame else current()
        key = game_key(g) if g else ""
        p.status({"searching": "queue", "session": "playing"}.get(state, "idle"), key if state != "idle" else "",
                 host_abilities(cfg, g) if state != "idle" else None)

    def resend_status():
        """Tell friends' apps again what this computer can host (e.g. right after the streaming tools were installed)."""
        presence_status(social_state.get("state", engine.state))

    def recheck_streaming():
        """Streaming said 'not ready' earlier? Check again quietly (at most once a minute) and tell friends if it changed.
        So installing the tools works without restarting the app."""
        if cfg.get("stream_backend", "own") != "own" or _own_cache.get("v", (False,))[0]:
            return
        now = time.monotonic()
        if social_state.get("stream_checking") or now - social_state.get("stream_checked", -1e9) < 60:
            return
        social_state["stream_checking"], social_state["stream_checked"] = True, now

        def work():
            ok = own_host_ready(refresh=True)[0]
            guest_ready(refresh=True)
            social_state["stream_checking"] = False
            if ok:
                msgs.put(("call", None, lambda: (resend_status(), refresh())))
        threading.Thread(target=work, daemon=True).start()

    def game_title(key):
        g = next((x for x in cfg["games"] if game_key(x) == key), None)
        return g["name"] if g else (key.split(":", 1)[-1].replace("-", " ").title() or "a game")

    def add_friend_from(f):
        """f from parse_friend: a short code (look it up first) or an id."""
        p = social_state["p"]
        if f.get("code"):
            if not p or not social_state["up"]:
                return status.config(text="Connect to a matchmaker first (Friends... shows how), then add friends by code.")
            social_state["want"] = f["code"]
            p.lookup(f["code"])
            return status.config(text=f"Looking up {social.pretty_code(f['code'])}...")
        send_request({"id": f["id"], "name": f.get("name", "Friend"), "code": ""})

    def send_request(f):
        res = social.add_friend(cfg, {**f, "pending": True})
        if res == "self":
            return status.config(text="That's your own friend code.")
        mine = social.find(cfg, f["id"])
        if res == "known" and not mine.get("pending"):
            return status.config(text=f"{mine['name']} is already your friend.")
        friends_changed()
        if social_state["p"]:
            social_state["p"].request(f["id"])
        status.config(text=f"Friend request sent to {f['name']}. You'll be friends when they accept.")

    def answer_request(r, ok):
        cfg["friend_requests"] = [x for x in cfg["friend_requests"] if x["id"] != r["id"]]
        toasts.close("req:" + r["id"])
        if ok:
            social.add_friend(cfg, {"id": r["id"], "name": r["name"], "code": r.get("code", "")})
            if social_state["p"]:
                social_state["p"].accept(r["id"])
            status.config(text=f"You and {r['name']} are friends now.")
        friends_changed()

    def join_friend(f):
        """Ask a friend to let you into their game. They tap Allow; then they host and this computer connects."""
        p = social_state["p"]
        if not p or not social_state["up"]:
            return status.config(text="You're not connected to the matchmaker yet. Open Friends... to see why.")
        if f.get("pending"):
            return status.config(text=f"{f['name']} hasn't accepted your friend request yet.")
        on = social_state["online"].get(f["id"])
        if not on:
            return status.config(text=f"{f['name']} isn't online right now.")
        theirs = on.get("game") if on.get("state") in ("playing", "queue") else ""
        mine = next((x for x in cfg["games"] if game_key(x) == theirs), None) if theirs else None
        if theirs and not mine:
            return status.config(text=f"{f['name']} is playing {game_title(theirs)}, which isn't in your games. "
                                      "Add it first, with the same name.")
        g = mine or current()
        if not g:
            return status.config(text="Add a game first.")
        if theirs:
            problem = join_problem(on.get("can") or {}, g)
            if problem:
                return status.config(text=problem.replace("{name}", f["name"]))
        if not p.call(f["id"], game_key(g)):
            return status.config(text="Couldn't reach the matchmaker. Try again in a moment.")
        status.config(text=f"Asking {f['name']} to let you into {g['name']}... They'll see Allow / Deny.")

    def on_social(ev, d):
        p = social_state["p"]
        if ev == "up":
            social_state["up"], social_state["why"] = True, ""
            if d.get("code") and cfg.get("my_code") != d["code"]:
                cfg["my_code"] = d["code"]
                save_cfg(cfg)
        elif ev == "down":
            social_state["up"], social_state["online"], social_state["why"] = False, {}, str(d)
            auto_find()                         # the matchmaker went away or moved: look for it again
        elif ev == "online":
            social_state["online"] = {x["id"]: x for x in d}
            changed = False
            for x in d:                         # seeing each other online means both added: no longer pending
                f = social.find(cfg, x["id"])
                if f and (f.get("pending") or (x.get("code") and f.get("code") != x["code"])):
                    f["pending"], f["code"], changed = False, social.pretty_code(x.get("code")) or f.get("code", ""), True
            if changed:
                save_cfg(cfg)
        elif ev == "lookup":
            if social_state.get("want") != social.short(d["code"]):
                return
            social_state["want"] = None
            if not d["found"]:
                status.config(text=f"No player has the friend code {d['code']} on this matchmaker ({invites.link_host(cfg)}). "
                                   "Check the code, and that your friend's app uses this same matchmaker.")
            elif d["self"]:
                status.config(text="That's your own friend code.")
            else:
                send_request({"id": d["id"], "name": d["name"], "code": d["code"]})
        elif ev == "friend_request":
            if social.got_request(cfg, d) == "friends":
                status.config(text=f"You and {d['name']} are friends now.")
                if p:
                    p.accept(d["id"])
                friends_changed()
            else:
                save_cfg(cfg)
                r = {"id": d["id"], "name": d["name"], "code": d.get("code", "")}
                toasts.show("Friend request", f"{d['name']} ({d['code'] or 'no code'}) wants to be friends.",
                            [("Accept", lambda r=r: answer_request(r, True), True),
                             ("Decline", lambda r=r: answer_request(r, False), False),
                             ("Later", lambda: None, False)], timeout=45, key="req:" + d["id"])
                status.config(text=f"{d['name']} ({d['code']}) wants to be friends.")
        elif ev == "request_sent":
            pass
        elif ev == "friend_accept":
            f = social.find(cfg, d["id"])
            if f:
                f["pending"], f["name"] = False, d["name"]
                f["code"] = d["code"] or f.get("code", "")
                status.config(text=f"{d['name']} accepted your friend request.")
                toasts.show("Friends now", f"{d['name']} accepted your friend request. When they're online, "
                            "pick them in Friends and press Join.", [("OK", lambda: None, False)], timeout=10, key="acc:" + d["id"])
                friends_changed()
        elif ev == "soft_error":
            status.config(text=f"Friends: {d}")
        elif ev == "call" and p:
            title = game_title(d["game"])
            why = engine.join_refusal(d["game"])
            if why:
                theirs, mine = {"no_game": (f"{cfg['my_name']} doesn't have {title} in their games.",
                                            "it isn't in your games (same name needed)"),
                                "busy": (f"{cfg['my_name']} is already playing with someone.",
                                         "you're already playing with someone")}[why]
                p.answer(d["from"], d["call"], False, theirs)
                status.config(text=f"{d['name']} wanted to join you in {title}, but {mine}.")
            else:
                ans = {"done": False}

                def answer(ok, d=d):
                    if not ans["done"]:
                        ans["done"] = True
                        if social_state["p"]:
                            social_state["p"].answer(d["from"], d["call"], ok)
                        status.config(text=(f"Letting {d['name']} in. You host; they connect to you." if ok
                                            else f"You said no to {d['name']}."))
                toasts.show("Join request", f"{d['name']} wants to join you in {title}.",
                            [("Allow", lambda: answer(True), True), ("Deny", lambda: answer(False), False)],
                            timeout=55, key="call:" + d["call"], on_timeout=lambda: answer(False))
                if engine.ingame and SYSTEMS[engine.ingame["game"]["system"]]["emu"] == "retroarch":
                    ra_cmd(f"SHOW_MSG Retro-Netplay: {d['name']} wants to join. Hold Start + Select to allow.")
        elif ev == "cancelled":
            toasts.close("call:" + str(d))
        elif ev == "start":
            if d.get("as") == "callee":
                status.config(text=f"Starting {game_title(d['game'])} for you and {d['with']}. You host.")
            else:
                status.config(text=f"{d['with']} let you in. Connecting to their {game_title(d['game'])}...")
            engine.start_call(d)
        elif ev == "declined":
            status.config(text=d["why"] or f"{d['name']} said no this time.")
        elif ev == "failed":
            status.config(text="That friend isn't online right now." if d == "not online" else f"The join didn't go through: {d}")
        redraw_friends()

    def friend_link_text():
        host = (cfg["invite"].get("host") or cfg.get("server_host") or "").strip()
        return social.friend_link(host, int(cfg["server_port"]), cfg.get("my_code", ""), cfg["my_name"])

    def open_friends():
        win = tk.Toplevel(root)
        win.title("Friends")
        win.transient(root)
        body = ttk.Frame(win, padding=14)
        body.pack(fill="both", expand=True)

        conn = ttk.Label(body, text="", font=(fam, 10, "bold"), wraplength=520, justify="left")
        conn.pack(anchor="w")
        conn_note = ttk.Label(body, text="", foreground=ink.SOFT, wraplength=520, justify="left")
        conn_note.pack(anchor="w")
        def try_again():
            if auto_matchmaker_on(cfg):
                auto_find(force=True)
            else:
                restart_presence()
        again_btn = ttk.Button(body, text="Try again", command=try_again)

        ttk.Label(body, text="YOUR FRIEND CODE", font=(fam, 9, "bold")).pack(anchor="w", pady=(12, 0))
        code_row = ttk.Frame(body)
        code_row.pack(fill="x")
        code_lbl = ttk.Label(code_row, text="", font=(fam, 22, "bold"))
        code_lbl.pack(side="left")

        def copy_code():
            if not cfg.get("my_code"):
                return
            root.clipboard_clear()
            root.clipboard_append(f"Add me on Retro-Netplay: {cfg['my_code']}  ({friend_link_text()})")
            status.config(text="Your friend code is copied. Send it to a friend.")
        ttk.Button(code_row, text="Copy", command=copy_code).pack(side="left", padx=(12, 0))
        name_row = ttk.Frame(body)
        name_row.pack(fill="x", pady=(6, 0))
        ttk.Label(name_row, text="Your name").pack(side="left")
        name_var = tk.StringVar(value=cfg["my_name"])
        ttk.Entry(name_row, textvariable=name_var, width=20).pack(side="left", padx=6)

        def save_name():
            n = social.clean_name(name_var.get())
            if n and n != cfg["my_name"]:
                cfg["my_name"] = n
                save_cfg(cfg)
                restart_presence()
                status.config(text="Name saved.")
        ttk.Button(name_row, text="Save", command=save_name).pack(side="left")

        ttk.Label(body, text="ADD A FRIEND", font=(fam, 9, "bold")).pack(anchor="w", pady=(14, 0))
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(4, 0))
        add_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=add_var, width=12, font=(fam, 14))
        ent.pack(side="left")

        def add(_e=None):
            f = social.parse_friend(add_var.get())
            if not f:
                return status.config(text="Type your friend's code, like K7M-4QZ.")
            add_var.set("")
            add_friend_from(f)
        ent.bind("<Return>", add)
        ttk.Button(bar, text="Send request", command=add).pack(side="left", padx=6)
        ttk.Label(bar, text="their code, like K7M-4QZ", foreground=ink.SOFT).pack(side="left", padx=6)

        req_box = ttk.Frame(body)
        req_box.pack(fill="x", pady=(10, 0))

        ttk.Label(body, text="FRIENDS", font=(fam, 9, "bold")).pack(anchor="w", pady=(12, 0))
        lst = tk.Listbox(body, height=8, font=(fam, 11), activestyle="none", exportselection=False,
                         bg="#efeeea", fg=ink.INK, relief="solid", borderwidth=1, highlightthickness=0,
                         selectbackground=ink.INK, selectforeground=ink.PAPER)
        lst.pack(fill="both", expand=True, pady=(4, 0))
        acts = ttk.Frame(body)
        acts.pack(fill="x", pady=(8, 0))

        def chosen():
            i = lst.curselection()
            return cfg["friends"][i[0]] if i and i[0] < len(cfg["friends"]) else None

        def join():
            f = chosen()
            if not f:
                return status.config(text="Pick a friend in the list first.")
            join_friend(f)

        def remove():
            f = chosen()
            if f and messagebox.askyesno("Remove friend", f"Remove {f['name']}?", parent=win):
                cfg["friends"].remove(f)
                friends_changed()
        join_btn = ttk.Button(acts, text="Join", command=join)
        join_btn.pack(side="left")
        lst.bind("<Double-Button-1>", lambda e: join())
        ttk.Label(acts, text="They tap Allow, then you connect to their game.", foreground=ink.SOFT).pack(side="left", padx=8)
        ttk.Button(acts, text="Remove", command=remove).pack(side="right")

        def draw():
            host = str(cfg.get("server_host", "")).strip()
            port = int(cfg["server_port"])
            here = lan.is_loopback(host)
            name = "this computer" if here else (cfg.get("server_name") or host)
            if social_state["up"]:
                conn.config(text="Connected" + (" · this computer is the matchmaker" if here else f" · using the matchmaker on {name}"))
                conn_note.config(text="Phones and computers on your Wi-Fi find it by themselves: they just open Friends.")
            elif social_state.get("finding"):
                conn.config(text="Looking for the matchmaker on your Wi-Fi...")
                conn_note.config(text="")
            elif cfg.get("run_server") and _local_mm.get("err"):
                conn.config(text="The matchmaker couldn't start on this computer")
                conn_note.config(text=f"{_local_mm['err']}. Another program may be using port {port}. Restart the computer, then open Retro-Netplay again.")
            elif no_matchmaker_hint(cfg):
                conn.config(text="Not connected")
                conn_note.config(text="No Retro-Netplay found on your Wi-Fi yet. Open it on the computer you play on, then press Try again.")
            else:
                conn.config(text=f"Can't reach the matchmaker on {name}")
                why = social_state["why"]
                conn_note.config(text=(f"({why}) " if why else "") + "Is Retro-Netplay open on that computer, on the same Wi-Fi? "
                                      "Retro-Netplay keeps looking by itself, or press Try again.")
            if social_state["up"]:
                again_btn.pack_forget()
            elif not again_btn.winfo_manager():
                again_btn.pack(anchor="w", pady=(4, 0), after=conn_note)
            code_lbl.config(text=cfg.get("my_code") if social_state["up"] and cfg.get("my_code") else "···-···")
            for w in req_box.winfo_children():
                w.destroy()
            for r in cfg.get("friend_requests", []):
                row = ttk.Frame(req_box)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=f"{r['name']} ({r.get('code') or 'no code'}) wants to be friends").pack(side="left")
                ttk.Button(row, text="Decline", command=lambda r=r: answer_request(r, False)).pack(side="right")
                ttk.Button(row, text="Accept", command=lambda r=r: answer_request(r, True)).pack(side="right", padx=6)
            sel = lst.curselection()
            lst.delete(0, "end")
            for f in cfg["friends"]:
                on = social_state["online"].get(f["id"])
                if f.get("pending"):
                    where = "waiting for them to accept"
                elif not on:
                    where = "offline"
                elif on.get("state") in ("playing", "queue") and on.get("game"):
                    where = f"playing {game_title(on['game'])}"
                else:
                    where = "online"
                twins = sum(1 for x in cfg["friends"] if x["name"] == f["name"]) > 1
                label = f"{f['name']} ({f['code']})" if twins and f.get("code") else f["name"]
                lst.insert("end", f" {'●' if on else '○'} {label}  —  {where}")
            if not cfg["friends"]:
                lst.insert("end", " No friends yet. Share your code, or type a friend's code above.")
            if sel and sel[0] < lst.size():
                lst.selection_set(sel[0])
        social_state["redraw"] = lambda: draw() if win.winfo_exists() else None
        win.protocol("WM_DELETE_WINDOW", lambda: (social_state.__setitem__("redraw", None), win.destroy()))
        draw()

    def open_invites():
        win = tk.Toplevel(root)
        win.title("Invites & social")
        win.transient(root)
        win.grab_set()
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        texts, flags = [], []

        def field(label, store, key, secret=False):
            r = ttk.Frame(body)
            r.pack(fill="x", pady=3)
            ttk.Label(r, text=label, width=26).pack(side="left")
            v = tk.StringVar(value=str(store.get(key, "")))
            ttk.Entry(r, textvariable=v, width=34, show="*" if secret else "").pack(side="left", fill="x", expand=True)
            texts.append((store, key, v))

        def flag(label, store, key):
            v = tk.BooleanVar(value=bool(store.get(key)))
            ttk.Checkbutton(body, text=label, variable=v).pack(anchor="w", pady=1)
            flags.append((store, key, v))

        def note(text):
            ttk.Label(body, text=text, foreground="#666", wraplength=470, justify="left").pack(anchor="w", pady=(0, 6))

        ttk.Label(body, text="Invite link", font=("", 10, "bold")).pack(anchor="w")
        flag("Make an invite link when I start searching", cfg["invite"], "enabled")
        flag("Also let random players match me (untick for friends only)", cfg["invite"], "open_to_randoms")
        field("Public address for the link", cfg["invite"], "host")
        note("Friends open the link, so it must use an address they can reach (a domain name or your public IP). "
             "Anyone with the link can see that address. Leave blank to use the matchmaker address from Settings.")
        ttk.Label(body, text="Post my invite for me (optional)", font=("", 10, "bold")).pack(anchor="w", pady=(8, 0))
        note("Connect a place to post. Only your invite link and the message below are ever sent, never your files, "
             "settings or controllers. The secrets are saved only on this device. Anyone who has them can post as you, "
             "so don't share them. Apps like X, Instagram and Facebook don't allow this kind of posting: use Copy link instead.")
        field("Discord webhook address", cfg["social"], "discord_webhook", secret=True)
        field("Telegram bot token", cfg["social"], "telegram_token", secret=True)
        field("Telegram chat id", cfg["social"], "telegram_chat")
        flag("Post automatically when I start searching (off until you tick this)", cfg["invite"], "auto_announce")
        field("Minutes between posts", cfg["invite"], "cooldown_min")
        field("Message ({game} and {link})", cfg["invite"], "template")

        def apply():
            for store, key, v in texts:
                store[key] = v.get().strip()
            for store, key, v in flags:
                store[key] = v.get()
            try:
                cfg["invite"]["cooldown_min"] = max(1, int(cfg["invite"]["cooldown_min"]))
            except (TypeError, ValueError):
                cfg["invite"]["cooldown_min"] = 15
            save_cfg(cfg)

        def test_post():
            apply()
            sample = invites.build_link(cfg, "test:test-game", invites.new_code())
            status.config(text="Sending a test post...")
            def work():
                res = invites.announce(cfg, "a test game", sample, force=True)
                save_cfg(cfg)
                msgs.put(("note", "Test post: " + "; ".join(f"{n}: {'sent' if ok else note_}" for n, ok, note_ in res), None))
            threading.Thread(target=work, daemon=True).start()

        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="Send a test post", command=test_post).pack(side="left")
        ttk.Button(bar, text="Save", command=lambda: (apply(), win.destroy(), refresh())).pack(side="right")

    colors = {"idle": "play", "searching": "search", "session": "close"}
    labels = {"idle": "Play", "searching": "Leave queue", "session": "End session"}

    def pump():
        try:
            while True:
                state, text, clip = msgs.get_nowait()
                if state == "call":
                    clip()
                    continue
                if state == "social":
                    on_social(text, clip)
                    continue
                if state == "share":
                    toasts.show("Streaming to your friend", text, [("OK", lambda: None, False)], timeout=25, key="share")
                    continue
                if state == "link":
                    link_var.set(text)
                    if text:
                        link_row.pack(fill="x", after=status)    # only shown while there's an invite link
                    else:
                        link_row.pack_forget()
                    if text:
                        root.clipboard_clear()
                        root.clipboard_append(text)
                    continue
                if state in colors:
                    go_btn.set(labels[state], colors[state], loop=(state == "searching"))
                    presence_status(state)
                    sq_state["searching"] = state == "searching"
                    if not sq_state["searching"]:
                        draw_squares()
                if clip:
                    root.clipboard_clear()
                    root.clipboard_append(clip)
                if state == "hotkey":
                    refresh()
                if state == "note":
                    refresh()
                    hint.config(text=text)
                else:
                    status.config(text=text)
        except queue.Empty:
            pass
        root.after(120, pump)

    def open_wizard(only=None):
        import wizard
        ctx = {"autodetect": autodetect, "emu_ok": emu_ok, "EMUS": EMUS, "SYSTEMS": SYSTEMS,
               "refresh_ports": refresh_ports, "controller_check": lambda: controller_check(),
               "done": lambda: (refresh(), save_cfg(cfg), ensure_local_server(cfg, mm_note), restart_presence(),
                                menu_entry(), start_background())}
        wizard.run(root, tk, ttk, filedialog, cfg, ctx, only=only)

    def menu_entry():
        """Put the app in the app menu (once chosen in setup), and follow it if the download was moved."""
        if cfg.get("menu_entry", True) and cfg.get("setup_done") and not runtime.menu_entry_points_here():
            msg = runtime.install_menu_entry()
            if msg:
                hint.config(text=msg)
        msg = runtime.register_link_handler()     # Windows: netplay:// links open this app
        if msg:
            hint.config(text=msg)

    def start_background():
        """Pick up new games in the folders and keep the router ports open, without slowing the window."""
        def work():
            if cfg.get("game_folders"):
                library_games, _q = library.scan(cfg["game_folders"], cfg.get("folder_answers", {}))
                merged = library.merge(cfg["games"], library_games)
                msgs.put(("call", None, lambda: (cfg.__setitem__("games", merged), refresh())))
            res = refresh_ports(cfg)
            bad = [v["message"] for v in res.values() if v["state"] in ("closed", "failed")]
            if bad:
                msgs.put(("note", bad[0], None))
            if cfg.get("stream_backend", "own") == "own" and not guest_ready(refresh=True)[0]:
                msgs.put(("call", None, refresh))     # the setup check then says why joining a stream won't work yet
        threading.Thread(target=work, daemon=True).start()

    refresh()
    pump()
    tick()
    restart_presence()
    root.after(1500, lambda: auto_find(force=True))   # after this computer's own matchmaker is up
    root.after(30000, keep_finding)
    def open_args(args):
        given = [a for a in args if a.startswith("netplay://") or "/join?" in a or "/friend?" in a]
        root.deiconify()
        root.lift()
        if given:
            join_with_link(given[0])
    instance.listen(lambda args: msgs.put(("call", None, lambda: open_args(args))))
    try:
        root.createcommand("::tk::mac::LaunchURL", lambda *urls: open_args(list(urls)))   # macOS hands links over this way
    except tk.TclError:
        pass
    if any(a.startswith("netplay://") or "/join?" in a or "/friend?" in a for a in sys.argv[1:]):
        root.after(800, lambda: open_args(sys.argv[1:]))
    if not cfg.get("setup_done"):
        root.after(300, open_wizard)
    else:
        menu_entry()
        start_background()
    # for automatic screenshots of the packaged build: NETPLAY_SHOW=friends|settings|controllers
    show = os.environ.get("NETPLAY_SHOW", "")
    if show == "streamcheck":     # Streaming check opened from Settings (the smoke test clicks its Close button)
        root.after(1500, lambda: (open_settings(), root.after(800, show_stream_check)))
    if show in ("friends", "settings", "controllers", "address", "card"):
        root.after(1500, {"friends": lambda: open_friends(), "settings": lambda: open_settings(),
                          "controllers": lambda: open_controllers(), "address": lambda: open_address(),
                          "card": lambda: toasts.show("Join request", "Odin wants to join you in F-Zero GX.",
                                                      [("Allow", lambda: None, True), ("Deny", lambda: None, False)])}[show])
    root.mainloop()
    save_cfg(cfg)
    if social_state["p"]:
        social_state["p"].stop()
    PORTS.close_all()


if __name__ == "__main__":
    run_gui()
