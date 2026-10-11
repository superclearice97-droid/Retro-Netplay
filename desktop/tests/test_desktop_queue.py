"""Desktop: Play starts RetroArch and queues; a match makes the in-game player host (network command, no restart)
and the other player reconnect with --connect; the hotkey leaves the queue but keeps the game; friends first;
a friend's accepted call meets in a private room; social helpers match the Android rules."""
import asyncio, json, os, socket, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
home = tempfile.mkdtemp(); os.environ["HOME"] = home
import app, server as mm, social

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)

d = Path(home)
log = d / "log.txt"
fake = d / "retroarch.sh"; fake.write_text('#!/bin/sh\necho "$@" >> "$LOGFILE"\nsleep 30\n'); fake.chmod(0o755)
os.environ["LOGFILE"] = str(log)
cores = d / "cores"; cores.mkdir(); (cores / f"mgba_libretro{app.LIB_EXT}").write_text("core")
rom = d / "Sonic Battle (USA).gba"; rom.write_text("rom")
app.NATIVE_GUEST_DELAY = 0.2
app.own_host_ready = lambda refresh=False: (False, "no")

# pretend RetroArch network commands
ra = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); ra.bind(("127.0.0.1", 0))
app.RA_CMD_PORT = ra.getsockname()[1]
got = []
threading.Thread(target=lambda: [got.append(ra.recvfrom(4096)[0].decode()) for _ in iter(int, 1)], daemon=True).start()

PORT = 7830
threading.Thread(target=lambda: asyncio.run(mm.main(PORT)), daemon=True).start(); time.sleep(0.6)

def mk(name, friends=(), secret=None):
    cfg = json.loads(json.dumps(app.DEFAULT_CFG))
    cfg.update(server_port=PORT, stream_backend="own")
    cfg["paths"] = {"retroarch": str(fake), "cores": str(cores)}
    cfg["games"] = [{"name": "Sonic Battle", "system": "gba", "rom": str(rom)}]
    cfg["invite"]["enabled"] = False
    cfg["secret"] = secret or social.new_secret(); cfg["my_name"] = name
    social.ensure_identity(cfg)
    for f in friends: social.add_friend(cfg, f)
    events = []
    return cfg, events, app.Engine(lambda c=cfg: c, lambda s, m, clip=None: events.append((s, m)))

def wait(cond, t=6):
    end = time.time() + t
    while time.time() < end:
        if cond(): return True
        time.sleep(0.05)
    return False

# 1. Play: boot, queue, host in the running game; the other player reconnects
ca, ea, A = mk("Ann"); cb, eb, B = mk("Bea")
A.play_game()
check("Play starts RetroArch with the game and network commands", wait(lambda: log.exists() and "Sonic Battle" in log.read_text()))
first = log.read_text().strip().splitlines()[0]
check("first launch has no netplay flags (just the game)", "--host" not in first and "--connect" not in first and "--appendconfig" in first, first)
cfgfile = first.split("--appendconfig")[1].strip().split()[0]
check("RetroArch config turns network commands on", 'network_cmd_enable = "true"' in Path(cfgfile).read_text())
check("in the queue while playing", wait(lambda: A.state == "searching") and A.ingame_alive())
B.toggle()
check("matched", wait(lambda: any("Matched with" in m for _, m in ea) and any("Matched with" in m for _, m in eb)))
check("player already in the game hosts by network command, no restart", wait(lambda: "NETPLAY_HOST_TOGGLE" in got) and A.ingame_alive(), got)
check("on-screen message names the opponent", any(x.startswith("SHOW_MSG") and "Bea" in x for x in got), got)
check("the other player starts RetroArch connecting to the host", wait(lambda: any("--connect 127.0.0.1" in l for l in log.read_text().splitlines())), log.read_text())
check("opponent's name shown", any("Matched with Ann" in m for _, m in eb), [m for _, m in eb][-2:])
A.stop(); B.stop(); time.sleep(0.4)

# 2. hotkey: leave the queue, keep the game; again: back in the queue
log.write_text("")
cc, ec, C = mk("Cy")
C.play_game()
wait(lambda: C.state == "searching")
C.hotkey()
check("hotkey leaves the queue but the game keeps running", wait(lambda: C.state == "session") and C.ingame_alive(), ec[-1])
C.hotkey()
check("hotkey again: back in the queue for the running game", wait(lambda: C.state == "searching") and C.ingame_alive())
C.stop(); time.sleep(0.2)
check("Leave queue keeps the game", C.state == "session" and C.ingame_alive())
C.stop(); time.sleep(0.3)
check("End session closes the game", wait(lambda: not C.ingame))

# 3. friends first (desktop sends the friend fields)
ann_s, bea_s = social.new_secret(), social.new_secret()
fa = {"id": social.id_of(ann_s), "name": "Ann"}; fb = {"id": social.id_of(bea_s), "name": "Bea"}
cs, es, S = mk("Stranger")
S.toggle(); time.sleep(0.3)
c1, e1, F1 = mk("Ann", [fb], ann_s)
F1.toggle(); time.sleep(0.6)
check("with friends you aren't matched with a stranger straight away", F1.state == "searching" and S.state == "searching", (e1[-2:], es[-2:]))
c2, e2, F2 = mk("Bea", [fa], bea_s)
F2.toggle()
check("friends match each other", wait(lambda: any("Matched with Bea (friend)" in m for _, m in e1)), [m for _, m in e1][-2:])
check("stranger keeps waiting", S.state == "searching")
for x in (S, F1, F2): x.stop()
time.sleep(0.3)

# 4. a call: both sides meet in the call's private room
c3, e3, P = mk("Ann", [fb], ann_s); c4, e4, Q = mk("Bea", [fa], bea_s)
P.start_call({"code": "abcd1234ef", "game": "gba:sonic-battle", "with": "Bea"})
Q.start_call({"code": "abcd1234ef", "game": "gba:sonic-battle", "with": "Ann"})
check("call room connects the two friends", wait(lambda: any("Matched with" in m for _, m in e3) and any("Matched with" in m for _, m in e4)))
check("searching message names the friend", any("Joining Bea for Sonic Battle" in m for _, m in e3), [m for _, m in e3][:3])
c5, e5, R = mk("Cy")
R.start_call({"code": "zzzz9999", "game": "ps2:not-mine", "with": "Bea"})
check("call for a game you don't have is explained", wait(lambda: any("don't have that game" in m for _, m in e5)))
for x in (P, Q): x.stop()

# 4b. Play on a GameCube game with no matchmaker yet: the game still starts, the app keeps retrying
log.write_text("")
iso = d / "F-Zero GX (USA).rvz"; iso.write_text("x")
app.RETRY_SECONDS = 1
free = socket.socket(); free.bind(("127.0.0.1", 0)); LATE = free.getsockname()[1]; free.close()
def mk_gc(name):
    cfg, ev, e = mk(name)
    cfg["paths"]["dolphin"] = str(fake)
    cfg["games"] = [{"name": "F-Zero GX", "system": "gamecube", "rom": str(iso)}]
    cfg["server_port"] = LATE; cfg["server_host"] = "127.0.0.1"; cfg["run_server"] = True   # "I'll run it on this computer"
    return cfg, ev, e
cg1, eg1, G1 = mk_gc("Gil")
started = []
G1.on_boot = lambda c: started.append(c["server_port"])
G1.play_game()
check("Play starts a GameCube game right away", wait(lambda: "-b -e" in log.read_text() and "F-Zero GX" in log.read_text()), log.read_text())
check("the window is asked to start its own matchmaker first", started == [LATE])
check("no matchmaker yet: still playing, retrying", wait(lambda: any("Trying again" in m for _, m in eg1)) and G1.ingame_alive(), eg1[-1:])
threading.Thread(target=lambda: asyncio.run(mm.main(LATE, 0)), daemon=True).start()
check("once the matchmaker is up, you're in the queue", wait(lambda: any(m.startswith("In the queue for F-Zero GX") for _, m in eg1)), [m for _, m in eg1][-2:])
cg2, eg2, G2 = mk_gc("Hal")
G2.toggle()
check("matched on the GameCube game", wait(lambda: any("Matched with" in m for _, m in eg1)))
lines = log.read_text().splitlines()
check("the solo game closes and Dolphin's own window opens for NetPlay (no -b)", wait(lambda: "" in log.read_text().splitlines()), lines)
check("queue messages in order (no second search running)", [m for _, m in eg1 if "Trying again" in m or m.startswith("In the queue")][-1].startswith("In the queue"),
      [m[:30] for _, m in eg1])
G1.stop(); G2.stop(); time.sleep(0.3)

# 5. social helpers agree with Android
s = "a" * 32
check("id matches the server", social.id_of(s) == mm.player_id(s))
check("typed code accepted", social.parse_friend(" " + social.pretty(fb["id"]).upper()) == {"id": fb["id"], "name": "Friend"})
check("web friend link parsed", social.parse_friend(f"http://1.2.3.4:7778/friend?i={fb['id']}&n=Bea%20%3Cx%3E") == {"id": fb["id"], "name": "Bea x"})
check("junk is not a friend", social.parse_friend("http://x/join?i=" + fb["id"]) is None and social.parse_friend("abc") is None)
cx = {"secret": s, "friends": []}; social.ensure_identity(cx)
check("adding yourself refused", social.add_friend(cx, {"id": social.id_of(s), "name": "me"}) == "self")
check("added, then known", social.add_friend(cx, fb) == "added" and social.add_friend(cx, fb) == "known")
cx["friends"][0]["join"] = True
check("join privilege auto-accepts play, not watch", social.auto_accept(cx, fb["id"], "play") and not social.auto_accept(cx, fb["id"], "watch"))
f = social.search_fields(cx, prefer_host=True)
check("search fields carry friends but no privileges except priority",
      f["friends"] == [fb["id"]] and f["priority"] == [] and "join" not in json.dumps(f) and f["prefer_host"] is True)
check("short friend code typed any way", social.parse_friend(" k7m 4qz ") == {"code": "K7M4QZ", "name": "Friend"}
      and social.pretty_code("k7m4qz") == "K7M-4QZ" and social.short("K7M-40Z") == "")
check("short-code friend link", social.parse_friend("http://1.2.3.4:7778/friend?c=K7M4QZ&n=Bea") == {"code": "K7M4QZ", "name": "Bea"})
check("friend link uses the short code", social.friend_link("1.2.3.4", 7777, "K7M-4QZ", "Sam B") == "http://1.2.3.4:7778/friend?c=K7M4QZ&n=Sam%20B")
cr = {"secret": "c" * 32, "friends": []}; social.ensure_identity(cr)
check("incoming request is listed", social.got_request(cr, {"id": fb["id"], "name": "Bea", "code": "K7M4QZ"}) == "new" and cr["friend_requests"][0]["code"] == "K7M-4QZ")
social.add_friend(cr, {"id": fb["id"], "name": "Bea"})
check("accepting removes the request and adds the friend", not cr["friend_requests"] and social.find(cr, fb["id"]))
social.add_friend(cr, {"id": fa["id"], "name": "Ann", "pending": True})
check("request to someone who asked you too makes you friends", social.got_request(cr, {"id": fa["id"], "name": "Ann"}) == "friends"
      and not social.find(cr, fa["id"])["pending"])
dol = app.refresh_ports.__code__.co_consts
old = json.loads(json.dumps(app.DEFAULT_CFG)); old.pop("hold_v2"); old["hold"] = 1.0
app.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True); app.CONFIG_PATH.write_text(json.dumps(old))
check("old 1 s hotkey hold becomes 3 s", app.load_cfg()["hold"] == 3.0)
old["hold"] = 2.0; app.CONFIG_PATH.write_text(json.dumps(old))
check("a hold the player chose is kept", app.load_cfg()["hold"] == 2.0)
print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
