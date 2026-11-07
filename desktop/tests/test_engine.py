"""The new app's engine, through its local API (as the interface uses it): two players on one computer, each with
their own engine; one runs the lobby and opens a room, the other sees it, asks to join and is let in, and they chat.
Also: the API refuses requests without the token or from another site (Host header), only serves the library's own
pictures, scans the game folders at start, and a netplay room tells a player who lacks the game."""
import json, os, re, subprocess, sys, tempfile, time, urllib.request, urllib.error
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.dirname(HERE)

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{str(info)[:800]}]" if info and not ok else ""), flush=True); ok or fails.append(name)

LOBBY = 7950
procs = []
def engine(name, games, extra):
    home = tempfile.mkdtemp()
    for d in (".netplay-matchmaker", "Games/GBA", "bin", ".config/retroarch/cores"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    ra = os.path.join(home, "bin", "retroarch")
    open(ra, "w").write("#!/bin/sh\necho \"$@\" >> \"$HOME/emulator.txt\"\nsleep 30\n"); os.chmod(ra, 0o755)
    open(os.path.join(home, ".config/retroarch/cores/mgba_libretro.so"), "w").close()
    for g in games:
        open(os.path.join(home, "Games/GBA", g + ".gba"), "w").close()
    cfg = {"setup_done": True, "age_ok": True, "my_name": name, "game_folders": [os.path.join(home, "Games")],
           "paths": {"retroarch": ra}, "auto_ports": False, **extra}
    json.dump(cfg, open(os.path.join(home, ".netplay-matchmaker/config.json"), "w"))
    env = dict(os.environ, HOME=home, PATH=os.path.join(home, "bin") + os.pathsep + os.environ["PATH"])
    p = subprocess.Popen([sys.executable, os.path.join(D, "engine.py"), "--no-browser"], env=env, stdout=subprocess.PIPE, text=True,
                         stderr=open(os.path.join(home, "engine.log"), "w"))
    procs.append(p)
    for line in p.stdout:
        if line.startswith("RETRO_NETPLAY_UI"):
            url = line.split()[1]
            m = re.match(r"(http://127\.0\.0\.1:\d+)/#t=([\w-]+)", url)
            return {"base": m.group(1), "t": m.group(2), "home": home}
    raise SystemExit("engine didn't start: " + open(os.path.join(home, "engine.log")).read())

def get(e, path, token=True, host=None):
    req = urllib.request.Request(e["base"] + path, headers={"X-Token": e["t"]} if token else {})
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as err:
        return err.code, b""

def state(e):
    return json.loads(get(e, "/api/state")[1])

def do(e, action, **kw):
    req = urllib.request.Request(e["base"] + "/api/do", data=json.dumps(dict(kw, action=action)).encode(),
                                 headers={"X-Token": e["t"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def until(e, cond, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        s = state(e)
        if cond(s):
            return s
        time.sleep(0.2)
    return state(e)

try:
    sam = engine("Sam", ["Sonic Battle (USA)", "Bomberman Tournament (USA)"], {"run_lobby": True, "lobby_port": LOBBY, "lobby": f"127.0.0.1:{LOBBY}"})
    check("the interface page is served", get(sam, "/", token=False)[0] == 200 and b"app.js" in get(sam, "/", token=False)[1])
    check("the API needs the token", get(sam, "/api/state", token=False)[0] == 403)
    check("another site can't use the API (Host header)", get(sam, "/api/state", host="evil.example:80")[0] == 403)
    check("only the library's pictures are served", get(sam, "/art?p=/etc/passwd&t=" + sam["t"], token=False)[0] == 404)
    check("files outside the interface folder aren't served", get(sam, "/../engine.py", token=False)[0] in (400, 404))
    s = until(sam, lambda s: s["lobby"]["connected"] and len(s["library"]) == 2)
    check("the game folders are scanned at start", sorted(g["name"] for g in s["library"]) == ["Bomberman Tournament", "Sonic Battle"], s["library"])
    check("connected to this computer's own lobby, as its moderator", s["lobby"]["connected"] and s["lobby"]["admin"], s["lobby"])
    bea = engine("Bea", ["Sonic Battle (USA)"], {"run_lobby": False, "lobby": f"127.0.0.1:{LOBBY}"})
    until(bea, lambda s: s["lobby"]["connected"] and s["library"])
    check("a second player's app isn't the moderator", not state(bea)["lobby"]["admin"])

    sonic = next(g["i"] for g in state(sam)["library"] if g["name"] == "Sonic Battle")
    r = do(sam, "post", game=sonic, seats=2, note="Battle mode", auto=False, public=True)
    s = until(sam, lambda s: s["myroom"])
    check("opening a room: netplay for a RetroArch game, and the game starts for the host",
          s["myroom"] and s["myroom"]["mode"] == "netplay" and "emulator" in s["playing"]["running"], (r, s["myroom"], s["playing"]))
    time.sleep(0.5)
    emu = open(os.path.join(sam["home"], "emulator.txt")).read() if os.path.exists(os.path.join(sam["home"], "emulator.txt")) else ""
    check("RetroArch was started hosting netplay", "--host" in emu and "Sonic Battle" in emu, emu)
    b = until(bea, lambda s: s["rooms"])
    check("Bea sees Sam's room in the arcade", b["rooms"] and b["rooms"][0]["title"] == "Sonic Battle" and b["rooms"][0]["note"] == "Battle mode", b["rooms"])
    do(bea, "join", room=b["rooms"][0]["id"])
    s = until(sam, lambda s: s["requests"])
    check("Sam gets Bea's request", s["requests"] and s["requests"][0]["player"]["name"] == "Bea", s["requests"])
    do(sam, "answer", req=s["requests"][0]["req"], accept=True)
    b = until(bea, lambda s: s["seat"] and s["seat"].get("seat") == 2)
    check("Bea is let in as Player 2", b["seat"] and b["seat"]["seat"] == 2, b["seat"])
    time.sleep(0.5)
    emu_b = open(os.path.join(bea["home"], "emulator.txt")).read() if os.path.exists(os.path.join(bea["home"], "emulator.txt")) else ""
    check("Bea's RetroArch connects to Sam's", "--connect" in emu_b and "--port 55435" in emu_b, emu_b)
    do(bea, "chat", to="lobby", text="hi everyone!")
    s = until(sam, lambda s: s["chat"]["lobby"])
    check("lobby chat arrives", s["chat"]["lobby"] and s["chat"]["lobby"][-1]["text"] == "hi everyone!", s["chat"])
    do(sam, "chat", to="room", text="ready?")
    b = until(bea, lambda s: s["chat"]["room"])
    check("room chat arrives", b["chat"]["room"] and b["chat"]["room"][-1]["text"] == "ready?", b["chat"])
    do(bea, "block", player=state(sam)["me"]["id"], name="Sam")
    check("blocking is saved", state(bea)["blocked"] and state(bea)["blocked"][0]["name"] == "Sam")
    do(sam, "close_room")
    b = until(bea, lambda s: not s["seat"] and not s["rooms"])
    check("closing the room frees everyone", not b["seat"] and not b["rooms"], (b["seat"], b["rooms"]))
    bomber = next(g["i"] for g in state(sam)["library"] if g["name"] == "Bomberman Tournament")
    do(sam, "post", game=bomber, seats=2, auto=True)
    b = until(bea, lambda s: s["rooms"])
    check("a room for a game Bea doesn't have is listed (the interface greys it out)", b["rooms"] and b["rooms"][0]["mode"] == "netplay")
    r = do(sam, "setup", name="", age_ok=True)
    check("a name is required", not r["ok"] and "name" in r["msg"])
    r = do(sam, "setup", name="Sam", age_ok=False)
    check("13+ is required", not r["ok"] and "13" in r["msg"])
finally:
    for p in procs:
        p.terminate()
    subprocess.run(["pkill", "-f", "sleep 30"], capture_output=True)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
