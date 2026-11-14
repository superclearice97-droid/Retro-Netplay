"""A stream room for real: ONE stream host for the whole room (one capture, one encoder), the real lobby, and the
real player for each guest.

Part 1 (the host itself): two players join the same room. Both get the video at full speed from the single capture,
each is told their own player number, and each player's button presses reach their OWN virtual controller and
nobody else's. When one leaves, the other keeps playing; a third player can take the free seat.

Part 2 (through the app): two engines on a virtual screen. Sam opens a stream room and the app starts one stream
host for it; Bea joins, the app opens her player window, and video of Sam's screen arrives in it.

The host's virtual controller is swapped for one that records what it's told (tests/recording_host.py); capture,
encoding, WebRTC and the player are the real thing. Skips without GStreamer, SDL or a (virtual) screen."""
import asyncio, json, os, re, secrets, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.dirname(HERE); sys.path.insert(0, D)
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    import sdl2          # noqa: F401  (the player needs it)
    missing = [e for e in ("webrtcbin", "x264enc", "appsrc", "ximagesrc") if not Gst.ElementFactory.find(e)]
except (ImportError, ValueError) as e:
    missing = [str(e)]
if missing:
    print("SKIP (needs GStreamer with WebRTC and x264, and SDL2:", ", ".join(missing) + ")"); sys.exit(0)
if not os.environ.get("DISPLAY"):
    if not shutil.which("xvfb-run"):
        print("SKIP (needs a screen, or xvfb-run for a virtual one)"); sys.exit(0)
    os.execvp("xvfb-run", ["xvfb-run", "-a", "-s", "-screen 0 800x600x24", sys.executable] + sys.argv)

os.environ["NETPLAY_DATA"] = tempfile.mkdtemp()
os.environ["NETPLAY_ICE_JSON"] = "[]"        # (this machine only: no STUN server needed)
import server

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{str(info)[-1500:]}]" if info and not ok else ""), flush=True); ok or fails.append(name)

PORT = 7975
threading.Thread(target=lambda: asyncio.run(server.main(PORT, 0, discovery=False)), daemon=True).start()
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", PORT), 0.2).close(); break
    except OSError:
        time.sleep(0.05)

class P:
    """A player's lobby connection (speaks the lobby protocol directly)."""
    def __init__(self, name):
        self.s = socket.create_connection(("127.0.0.1", PORT), 3); self.buf = b""; self.got = []
        self.send({"op": "lobby", "v": 2, "me": secrets.token_hex(16), "name": name})
        self.wait("welcome")
    def send(self, m): self.s.sendall((json.dumps(m) + "\n").encode())
    def wait(self, op, timeout=8):
        for i, m in enumerate(self.got):
            if m.get("op") == op: return self.got.pop(i)
        end = time.time() + timeout
        while time.time() < end:
            if b"\n" not in self.buf:
                self.s.settimeout(max(0.05, end - time.time()))
                try: chunk = self.s.recv(65536)
                except (socket.timeout, OSError): continue
                if not chunk: break
                self.buf += chunk; continue
            line, self.buf = self.buf.split(b"\n", 1)
            m = json.loads(line)
            if m.get("op") == op: return m
            self.got.append(m)
        return {}

procs = []
def player(key, *extra):
    """The real player, headless, pressing buttons on a pretend controller. -> Popen (its output is read at the end)"""
    p = subprocess.Popen([sys.executable, "-u", os.path.join(D, "stream_guest.py"), "--mm", f"127.0.0.1:{PORT}", f"--key={key}",
                          "--headless", "--test-input", *extra], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         env={**os.environ, "HOME": tempfile.mkdtemp()})
    procs.append(p)
    return p
def finish(p, timeout=40):
    try:
        return p.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        return p.communicate()[0] + "\n(player TIMED OUT)"
def fps(out):
    stats = [json.loads(l[6:]) for l in out.splitlines() if l.startswith("STATS ")]
    return max([s.get("fps") or 0 for s in stats] or [0])

try:
    # ================================================================ 1) the room host itself
    pads = tempfile.mktemp(suffix=".jsonl"); hostlog = tempfile.mktemp(suffix=".log")
    host = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "recording_host.py"), "--mm", f"127.0.0.1:{PORT}", "--room",
                             "--capture", "test", "--no-audio", "--encoder", "x264enc"], stdin=subprocess.PIPE,
                            stdout=open(hostlog, "w"), stderr=subprocess.STDOUT,
                            env={**os.environ, "NETPLAY_PAD_LOG": pads, "HOME": tempfile.mkdtemp()})
    procs.append(host)
    def tell(m):
        host.stdin.write((json.dumps(m) + "\n").encode()); host.stdin.flush()
    pc = P("PC Host")
    pc.send({"op": "post", "room": {"game": "ps2:urban-reign", "title": "Urban Reign", "system": "ps2", "mode": "stream", "seats": 3,
                                    "auto": True, "public": True}})
    room = pc.wait("posted")["room"]
    guests = {}
    def join(name):
        g = P(name); g.send({"op": "join", "room": room["id"]})
        acc, seated = g.wait("accepted"), pc.wait("seated")
        tell({"op": "add", "key": seated["session"]["key"], "slot": seated["seat"]})
        guests[name] = g
        return acc["seat"], acc["session"]["key"]
    seat_b, key_b = join("Bea")
    seat_c, key_c = join("Cy")
    time.sleep(1.0)
    pb = player(key_b, "--seconds", "5")
    time.sleep(1.5)                       # Cy arrives while Bea is already watching
    pcy = player(key_c, "--seconds", "12")
    out_b = finish(pb)
    check("Player 2 gets the video at full speed", fps(out_b) >= 50 and "you are Player 2" in out_b, out_b)
    # Bea has left (her 5 seconds are up); Dee takes the free seat while Cy is still playing
    guests["Bea"].send({"op": "leave"})
    pc.wait("left", 5)
    tell({"op": "remove", "slot": seat_b})
    seat_d, key_d = join("Dee")
    pd = player(key_d, "--seconds", "4")
    out_c, out_d = finish(pcy), finish(pd)
    check("Player 3 joined mid-game and gets the video at full speed too", fps(out_c) >= 50 and "you are Player 3" in out_c, out_c)
    check("Player 3 kept playing after Player 2 left", "GUEST ENDED: you left" in out_c and "TIMED OUT" not in out_c, out_c[-800:])
    check("a new player takes the free seat (Player 2 again) and gets video", seat_d == 2 and fps(out_d) >= 50 and "you are Player 2" in out_d, out_d)
    log = open(hostlog).read()
    check("the whole room used ONE capture and encoder", log.count("video: test -> x264enc") == 1, [l for l in log.splitlines() if "guest sees" not in l and "host spends" not in l and "friend's screen" not in l][-30:])
    rows = [json.loads(l) for l in open(pads)] if os.path.exists(pads) else []
    by_pad = {}
    for r in rows:
        by_pad.setdefault(r["pad"], []).append(r)
    check("each player's buttons reached their own controller", set(by_pad) == {"Netplay Virtual Pad (Player 2)", "Netplay Virtual Pad (Player 3)"}
          and all(any(r["buttons"] for r in v) for v in by_pad.values()), {k: len(v) for k, v in by_pad.items()})
    host.stdin.close()
    try:
        host.wait(8)
    except subprocess.TimeoutExpired:
        pass
    check("the host stops when the app that started it goes away", host.poll() is not None)
    pc.send({"op": "close"})

    # ================================================================ 2) through the app, on the virtual screen
    homes = []
    def engine(name, extra):
        home = tempfile.mkdtemp(); homes.append(home)
        for d in (".netplay-matchmaker", "Games/PS2", "bin"):
            os.makedirs(os.path.join(home, d), exist_ok=True)
        emu = os.path.join(home, "bin", "pcsx2-qt")
        open(emu, "w").write("#!/bin/sh\nsleep 120\n"); os.chmod(emu, 0o755)
        iso = bytearray(0x9500); iso[0x8001:0x8006] = b"CD001"; iso[0x8008:0x8013] = b"PLAYSTATION"
        open(os.path.join(home, "Games/PS2", "Urban Reign (USA).iso"), "wb").write(iso)
        cfg = {"setup_done": True, "age_ok": True, "my_name": name, "game_folders": [os.path.join(home, "Games")],
               "paths": {"pcsx2": emu}, "auto_ports": False, "lobby": f"127.0.0.1:{PORT}", "run_lobby": False, **extra}
        json.dump(cfg, open(os.path.join(home, ".netplay-matchmaker/config.json"), "w"))
        env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"), XDG_SESSION_TYPE="x11",
                   NETPLAY_HOST_SCRIPT=os.path.join(HERE, "recording_host.py"), NETPLAY_PAD_LOG=os.path.join(home, "pads.jsonl"))
        p = subprocess.Popen([sys.executable, os.path.join(D, "engine.py"), "--no-browser"], env=env, stdout=subprocess.PIPE, text=True,
                             stderr=open(os.path.join(home, "engine.log"), "w"))
        procs.append(p)
        for line in p.stdout:
            if line.startswith("RETRO_NETPLAY_UI"):
                m = re.match(r"(http://127\.0\.0\.1:\d+)/#t=([\w-]+)", line.split()[1])
                return {"base": m.group(1), "t": m.group(2), "home": home}
        raise SystemExit("engine didn't start: " + open(os.path.join(home, "engine.log")).read())
    def state(e):
        with urllib.request.urlopen(urllib.request.Request(e["base"] + "/api/state", headers={"X-Token": e["t"]}), timeout=5) as r:
            return json.loads(r.read())
    def do(e, action, **kw):
        req = urllib.request.Request(e["base"] + "/api/do", data=json.dumps(dict(kw, action=action)).encode(), headers={"X-Token": e["t"]})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    def until(e, cond, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            s = state(e)
            if cond(s): return s
            time.sleep(0.2)
        return state(e)
    def read(e, name):
        try:
            return open(os.path.join(e["home"], ".netplay-matchmaker", name), errors="replace").read()
        except OSError:
            return ""
    def file_says(e, name, text, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if text in read(e, name): return True
            time.sleep(0.5)
        return False

    sam = engine("Sam", {})
    until(sam, lambda s: s["lobby"]["connected"] and s["library"])
    bea = engine("Bea", {})
    until(bea, lambda s: s["lobby"]["connected"])
    r = do(sam, "post", game=0, seats=3, auto=True)
    s = until(sam, lambda s: s["myroom"])
    check("a PS2 game opens a stream room, and the game starts for the host", s["myroom"] and s["myroom"]["mode"] == "stream"
          and "emulator" in s["playing"]["running"], (r, s["myroom"], s["playing"]))
    b = until(bea, lambda s: s["rooms"])
    do(bea, "join", room=b["rooms"][0]["id"])
    until(bea, lambda s: s["seat"] and s["seat"].get("seat") == 2)
    s = until(sam, lambda s: "streamhost" in s["playing"]["running"])
    check("the app started the room's stream host", "streamhost" in s["playing"]["running"], s["playing"])
    check("Bea's player window opened", "player" in until(bea, lambda s: "player" in s["playing"]["running"])["playing"]["running"])
    check("the host captured Sam's screen once and Bea connected", file_says(sam, "stream-host.log", "connection: connected")
          and read(sam, "stream-host.log").count("video: x11 -> ") == 1, read(sam, "stream-host.log")[-1500:])
    check("video of Sam's screen arrives in Bea's player window", file_says(bea, "stream-guest.log", "[player] video: 800x600"),
          read(bea, "stream-guest.log")[-1500:])
    do(bea, "leave")
    check("leaving closes Bea's player", "player" not in until(bea, lambda s: "player" not in s["playing"]["running"])["playing"]["running"])
    check("the host hears Player 2 left and keeps the room's stream program for the next player",
          file_says(sam, "stream-host.log", "ending:", 10) and "streamhost" in state(sam)["playing"]["running"], read(sam, "stream-host.log")[-600:])
    do(sam, "close_room")
    check("closing the room stops the stream program", "streamhost" not in until(sam, lambda s: "streamhost" not in s["playing"]["running"])["playing"]["running"])
finally:
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for h in globals().get("homes", []):
        subprocess.run(["pkill", "-f", h], capture_output=True)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
