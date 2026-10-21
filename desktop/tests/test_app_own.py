"""Desktop app, built-in stream: the host engine launches emulator + stream_host with the matchmaker address and the
host's key; the guest engine opens the PLAYER WINDOW (never a web browser) with the guest's key; both keys work only
over the TCP stream connection; when the host's stream program fails, the friend's player is told its real error;
an older matchmaker gets a plain message; invites are netplay:// links with a code a friend can type; incompatible
backends never pair."""
import asyncio, json, os, sys, tempfile, threading, time, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
home = tempfile.mkdtemp(); os.environ["HOME"] = home
import app, invites, runtime, server as mm, social, stream_signal

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)

app.NATIVE_GUEST_DELAY = 0.3
d = Path(home)
fake = d / "fake_emu.sh"; fake.write_text('#!/bin/sh\necho "$0 $@" >> "$LOGFILE"\nsleep 4\n'); fake.chmod(0o755)
os.environ["LOGFILE"] = str(d / "log.txt")
iso = d / "Urban Reign (USA).iso"; iso.write_text("rom")
# a stand-in for the player window: connects with its key and waits for the host's goodbye, like the real one
player = d / "fake_player.py"
player.write_text(f"""import sys, threading
sys.path.insert(0, {str(Path(app.__file__).parent)!r})
import stream_signal
a = sys.argv; mm, key = a[a.index("--mm") + 1], next(x[6:] for x in a if x.startswith("--key="))
open({str(d / "player-args.txt")!r}, "w").write(" ".join(a[1:]))
done, got = threading.Event(), []
h, p = stream_signal.parse_address(mm)
s = stream_signal.TcpSignaling(h, p, key, on_msg=lambda m: (got.append(m), m.get("type") == "bye" and done.set()))
print("role", s.connect()["role"], flush=True)
s.start(); s.send({{"type": "hello"}})
done.wait(15)
bye = [m for m in got if m.get("type") == "bye"]
print("GUEST ENDED: " + ("The stream stopped on your friend's computer: " + bye[0]["why"] + "." if bye and bye[0].get("why") else "you left"), flush=True)
""")
runtime.guest_commands = lambda: [[sys.executable, str(player)]]
app._guest_cache.update(v=(True, "ok"), cmd=[sys.executable, str(player)])
PORT = 7810
threading.Thread(target=lambda: asyncio.run(mm.main(PORT)), daemon=True).start(); time.sleep(0.6)

app.own_host_ready = lambda refresh=False: (True, "ok")
real_offer = app.stream_offer
app.stream_offer = lambda cfg: (True, cfg.get("_host", False), real_offer(cfg)[2]) if cfg.get("stream_backend") == "own" else real_offer(cfg)
spawned = []
real_exec = asyncio.create_subprocess_exec
async def spy(*a, **k):
    spawned.append(a); return await real_exec(*a, **k)
app.asyncio.create_subprocess_exec = spy
# the host's stream program: fails at once with a real-looking reason (like a missing encoder)
host_fail = d / "fail_host.py"
host_fail.write_text('print("STREAM ERROR: the video encoder vah264enc doesn\'t work here (no VA device)", flush=True)\nimport sys; sys.exit(2)\n')
runtime.host_script = lambda: host_fail

def mk(backend="own", host=False, pid="", name=""):
    cfg = json.loads(json.dumps(app.DEFAULT_CFG))
    cfg.update(server_port=PORT, stream_backend=backend, parsec_peer_id=pid, _host=host, my_name=name)
    cfg["paths"] = {k: str(fake) for k in app.EMUS}
    cfg["games"] = [{"name": "Urban Reign", "system": "ps2", "rom": str(iso)}]
    cfg["invite"]["enabled"] = False
    social.ensure_identity(cfg)
    log = []
    return cfg, log, app.Engine(lambda c=cfg: c, lambda s, m, clip=None: log.append((s, m, clip)))

check("this app tells the matchmaker it sets up streams over TCP", app.stream_offer({"stream_backend": "own"})[2] == {"backend": "own", "sig": "tcp"})
check("the app no longer uses a web browser at all", not hasattr(app, "webbrowser"))

ch, lh, eh = mk(host=True, name="Odin"); cg, lg, eg = mk(host=False, name="Bea")
eh.toggle(); time.sleep(0.5); eg.toggle()
deadline = time.time() + 15
while time.time() < deadline and not (lg and lg[-1][0] == "idle"):
    time.sleep(0.2)
check("matched on the built-in stream", any("built-in stream" in m for _, m, _ in lh) and any("built-in stream" in m for _, m, _ in lg),
      str([m for _, m, _ in lh][-3:]))
pl = [a for a in spawned if str(player) in a]
check("the guest opened the player window (once), not a browser", len(pl) == 1, str(spawned))
pargs = (d / "player-args.txt").read_text() if (d / "player-args.txt").exists() else ""
check("the player got the matchmaker address, its key and the friend's name",
      f"--mm 127.0.0.1:{PORT}" in pargs and "--key=" in pargs and "--name=Odin" in pargs, pargs)
sh = [a for a in spawned if any(str(host_fail) == str(x) for x in a)]
check("host started the emulator for the stream", any(str(fake) in a[0] and a[1:3] == ("-batch", "-fullscreen") for a in spawned), str(spawned))
check("host started its stream program with the matchmaker address + key + slot",
      len(sh) == 1 and "--mm" in sh[0] and f"127.0.0.1:{PORT}" in sh[0] and any(x.startswith("--key=") for x in sh[0])
      and "--server" not in sh[0], str(sh))
gkey = pargs.split("--key=")[1].split()[0] if "--key=" in pargs else ""
try:
    urllib.request.urlopen(f"http://127.0.0.1:{PORT + 1}/cfg?t={gkey}", timeout=5); web = True
except urllib.error.HTTPError:
    web = False
check("the guest's key does not work on the web page way", not web)
glog = (d / ".netplay-matchmaker" / "stream-guest.log").read_text() if (d / ".netplay-matchmaker" / "stream-guest.log").exists() else ""
check("the player had the guest role over TCP", "role guest" in glog, glog)
check("the friend's player was told the host's real error",
      "the video encoder vah264enc doesn't work here (no VA device)" in glog, glog)
check("...and the guest's app shows it when the player closes",
      lg and lg[-1][0] == "idle" and "vah264enc doesn't work here" in lg[-1][1], lg[-1:])
time.sleep(1.0)
check("host session winds down when the stream program ends, with the reason",
      lh[-1][0] == "idle" and "vah264enc" in lh[-1][1], lh[-1][1])
eh.stop(); eg.stop()

# Friends > Join > they Allow: both apps meet in the call's room; the one who asked gets the player window by itself
spawned.clear(); (d / "player-args.txt").unlink()
cf, lf, ef = mk(host=True, name="Odin"); cc, lc, ec = mk(host=False, name="Bea")
ef.start_call({"code": "c0ffee0123456789", "game": "ps2:urban-reign", "with": "Bea", "as": "callee"}); time.sleep(0.4)
ec.start_call({"code": "c0ffee0123456789", "game": "ps2:urban-reign", "with": "Odin", "as": "caller"})
deadline = time.time() + 15
while time.time() < deadline and not (d / "player-args.txt").exists():
    time.sleep(0.2)
check("after Allow, the player window opens by itself on the joining side (nothing to click)",
      (d / "player-args.txt").exists() and any(str(player) in a for a in spawned) and any("You are the guest" in m for _, m, _ in lc), lc[-3:])
check("...and the friend who allowed it hosts", any("You are the host" in m for _, m, _ in lf), lf[-3:])
time.sleep(1.5); ef.stop(); ec.stop(); time.sleep(0.3)

# an older matchmaker (no TCP sessions): plain message, nothing opened
spawned.clear()
c, l, e = mk()
asyncio.run_coroutine_threadsafe(e.play_as_guest("127.0.0.1", PORT, {"key": "abcdefgh12", "play": "/play?t=abcdefgh12"}, "", {}), e.loop).result(5)
check("an older matchmaker: the guest is told to update it, and nothing opens",
      l[-1][0] == "idle" and "older version" in l[-1][1] and not spawned, l[-1:])

# invites: a netplay:// link, and a code a friend can type
ci, li, ei = mk(host=True); ci["invite"]["enabled"] = True; ci["invite"]["open_to_randoms"] = False
ei.toggle(); time.sleep(0.8)
link = next((m for s, m, _ in li if s == "link" and m), "")
parsed = invites.parse_link(link)
check("the invite is a netplay:// app link", link.startswith("netplay://join?") and parsed and parsed["game"] == "ps2:urban-reign", link)
shown = next((m for s, m, _ in li if s == "searching" and "code" in m), "")
check("the invite code is shown so it can be read out", parsed and invites.pretty_code(parsed["code"]) in shown, shown)
check("typing the code (any case, with or without the dash) finds the game",
      invites.lookup_code("127.0.0.1", PORT, invites.typed_code(" " + invites.pretty_code(parsed["code"]).lower() + " ")) == "ps2:urban-reign")
check("a code nobody is using finds nothing", invites.lookup_code("127.0.0.1", PORT, "ZZZZ2222") == "")
check("not a code", invites.typed_code("hello") == "" and invites.typed_code("ABCD-234") == "")
ei.stop(); time.sleep(0.3)

# two guests that can't host never pair; a Parsec user never pairs with a built-in user
c1, l1, e1 = mk(host=False); c2, l2, e2 = mk(host=False)
e1.toggle(); time.sleep(0.4); e2.toggle(); time.sleep(1.0)
check("two non-hosting players stay waiting", l1[-1][0] == "searching" and l2[-1][0] == "searching")
e1.toggle(); e2.toggle(); time.sleep(0.5)   # toggle cancels from the engine's own thread, like the real hotkey
cp, lp, ep = mk(backend="parsec", pid="PID1"); co, lo, eo = mk(host=True)
ep.toggle(); time.sleep(0.4); eo.toggle(); time.sleep(1.0)
check("Parsec user and built-in user are not paired", lp[-1][0] == "searching" and lo[-1][0] == "searching",
      f"{lp[-1]} / {lo[-3:]}")
ep.toggle(); eo.toggle(); time.sleep(0.3)

# readiness text
c, _, _ = mk(); g = c["games"][0]
check("built-in streaming needs no Parsec id", app.readiness(c, g) == (["stream"], ""))
c["stream_backend"] = "parsec"
check("Parsec backend still needs the id", app.readiness(c, g)[0] == [] and "Parsec" in app.readiness(c, g)[1])
app._guest_cache["v"] = (True, "[OK] everything")
c["stream_backend"] = "own"
check("with the built-in streamer, the setup check never talks about Parsec", not any("Parsec" in t for _, t in app.checklist(c, g)),
      app.checklist(c, g))
app._guest_cache["v"] = (False, "[OK] webrtcbin\n[X ] no H.264 video decoder. Install gstreamer1.0-libav (gst-libav).")
c["stream_backend"] = "own"
check("the setup check says when joining a stream won't work yet, and why",
      any(st is None and "no H.264 video decoder" in t for st, t in app.checklist(c, g)), app.checklist(c, g))
# the download's own player can't decode here (no graphics-card decoder), the computer's GStreamer can: use that
bundled = d / "bundled_check.py"; bundled.write_text('print("[X ] no H.264 video decoder."); raise SystemExit(1)\n')
system = d / "system_check.py"; system.write_text('print("[OK] video decoder: avdec_h264  (software)")\n')
runtime.guest_commands = lambda: [[sys.executable, str(bundled)], [sys.executable, str(system)]]
good, rep = app.guest_ready(refresh=True)
check("joining uses the first way of starting the player that can decode", good and app._guest_cache["cmd"][-1] == str(system), rep)
runtime.guest_commands = lambda: [[sys.executable, str(bundled)]]
good, rep = app.guest_ready(refresh=True)
check("...and says why when none can", not good and "no H.264 video decoder" in rep, rep)
print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
