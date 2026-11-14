"""The real thing: two copies of the app's engine, the real lobby, and the REAL RetroArch (mGBA core) on a virtual
screen. Sam opens a netplay room, Bea joins it, and RetroArch's own logs must say they are in one netplay session:
the host sees "Bea has joined as player 2", the guest "You have joined as player 2". Leaving closes Bea's RetroArch.

The game is a tiny test ROM made here (a GBA header and an endless loop, no logo and nobody's code).
Skips when RetroArch, its mGBA core or Xvfb isn't installed."""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.dirname(HERE)

CORE_DIRS = ["/usr/lib/x86_64-linux-gnu/libretro", "/usr/lib/libretro", "/usr/lib64/libretro"]
core_dir = next((d for d in CORE_DIRS if os.path.exists(os.path.join(d, "mgba_libretro.so"))), None)
if not shutil.which("retroarch") or not core_dir:
    print("SKIP (needs retroarch and its mGBA core: apt install retroarch libretro-mgba)"); sys.exit(0)
if not os.environ.get("DISPLAY"):
    if not shutil.which("xvfb-run"):
        print("SKIP (needs a screen, or xvfb-run for a virtual one)"); sys.exit(0)
    os.execvp("xvfb-run", ["xvfb-run", "-a", "-s", "-screen 0 800x600x24", sys.executable] + sys.argv)

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{str(info)[-1200:]}]" if info and not ok else ""), flush=True); ok or fails.append(name)

def test_rom(path):
    rom = bytearray(0x200)
    rom[0:4] = bytes.fromhex("2e0000ea")            # b 0xC0
    rom[0xA0:0xAC] = b"NETPLAYTEST "
    rom[0xAC:0xB0] = b"ZNTE"; rom[0xB0:0xB2] = b"00"; rom[0xB2] = 0x96
    rom[0xBD] = (-(sum(rom[0xA0:0xBD]) + 0x19)) & 0xFF
    rom[0xC0:0xC4] = bytes.fromhex("feffffea")      # b . (loop forever)
    open(path, "wb").write(rom)

LOBBY = 7970
procs, homes = [], []
def engine(name, extra):
    home = tempfile.mkdtemp(); homes.append(home)
    for d in (".netplay-matchmaker", "Games/GBA", ".config/retroarch", "ralogs"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    test_rom(os.path.join(home, "Games/GBA", "Netplay Test (USA).gba"))
    # RetroArch on a virtual screen with no sound card; its log goes where the test can read it
    open(os.path.join(home, ".config/retroarch/retroarch.cfg"), "w").write(
        'audio_driver = "null"\nvideo_driver = "sdl2"\nmenu_driver = "rgui"\nconfig_save_on_exit = "false"\npause_nonactive = "false"\n'
        'netplay_public_announce = "false"\nnetplay_nat_traversal = "false"\n'
        f'libretro_directory = "{core_dir}"\nlibretro_info_path = "/usr/share/libretro/info/"\n'
        f'log_to_file = "true"\nlog_to_file_timestamp = "false"\nlog_verbosity = "true"\nlog_dir = "{home}/ralogs"\n')
    cfg = {"setup_done": True, "age_ok": True, "my_name": name, "game_folders": [os.path.join(home, "Games")],
           "auto_ports": False, **extra}
    json.dump(cfg, open(os.path.join(home, ".netplay-matchmaker/config.json"), "w"))
    env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"),
               DBUS_FATAL_WARNINGS="0")      # (no desktop session here: RetroArch's GameMode check must not abort it)
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
        if cond(s):
            return s
        time.sleep(0.2)
    return state(e)
def ralog(e):
    try:
        return open(os.path.join(e["home"], "ralogs", "retroarch.log"), errors="replace").read()
    except OSError:
        return ""
def log_says(e, text, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        if text in ralog(e):
            return True
        time.sleep(0.5)
    return False

try:
    sam = engine("Sam", {"run_lobby": True, "lobby_port": LOBBY, "lobby": f"127.0.0.1:{LOBBY}"})
    s = until(sam, lambda s: s["lobby"]["connected"] and s["library"])
    check("the real RetroArch and its mGBA core are found by themselves", s["emulators"]["retroarch"]["ok"] and s["library"]
          and not s["library"][0]["problem"], (s["emulators"]["retroarch"], s["library"]))
    bea = engine("Bea", {"run_lobby": False, "lobby": f"127.0.0.1:{LOBBY}"})
    until(bea, lambda s: s["lobby"]["connected"] and s["library"])

    do(sam, "post", game=0, seats=2, auto=True)
    s = until(sam, lambda s: s["myroom"])
    check("Sam's room is a netplay room", s["myroom"] and s["myroom"]["mode"] == "netplay", s["myroom"])
    check("Sam's RetroArch is hosting netplay (its own log says so)", log_says(sam, "[Netplay] You have joined as player 1"), ralog(sam)[-1500:])
    b = until(bea, lambda s: s["rooms"])
    do(bea, "join", room=b["rooms"][0]["id"])
    until(bea, lambda s: s["seat"] and s["seat"].get("seat") == 2)
    check("the host's RetroArch: Bea joined as player 2, under her own name", log_says(sam, "Bea has joined as player 2"), ralog(sam)[-1500:])
    check("Bea's RetroArch: connected to Sam, playing as player 2", log_says(bea, "You have joined as player 2")
          and 'Connected to: "Sam"' in ralog(bea), ralog(bea)[-1500:])
    do(bea, "leave")
    gone = until(bea, lambda s: "emulator" not in s["playing"]["running"], 10)
    check("leaving the room closes Bea's RetroArch", "emulator" not in gone["playing"]["running"], gone["playing"])
    check("and the host's RetroArch notices", log_says(sam, '"Bea" has disconnected', 15), ralog(sam)[-800:])
    check("the host keeps playing", "emulator" in state(sam)["playing"]["running"])
finally:
    for p in procs:
        p.terminate()
    for h in homes:      # the RetroArch copies this test started (they were given these homes' game files)
        subprocess.run(["pkill", "-f", h], capture_output=True)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
