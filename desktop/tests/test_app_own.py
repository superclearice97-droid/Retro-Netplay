"""Desktop app, built-in stream: host engine launches emulator + stream_host with the right key; guest engine opens the
browser link; the link really serves the guest page; incompatible backends never pair."""
import asyncio, json, os, sys, tempfile, threading, time, urllib.request
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
home = tempfile.mkdtemp(); os.environ["HOME"] = home
import app, server as mm

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)

app.NATIVE_GUEST_DELAY = 0.3
d = Path(home)
fake = d / "fake_emu.sh"; fake.write_text('#!/bin/sh\necho "$0 $@" >> "$LOGFILE"\nsleep 4\n'); fake.chmod(0o755)
os.environ["LOGFILE"] = str(d / "log.txt")
iso = d / "Urban Reign (USA).iso"; iso.write_text("rom")
PORT = 7810
threading.Thread(target=lambda: asyncio.run(mm.main(PORT)), daemon=True).start(); time.sleep(0.6)

app.own_host_ready = lambda refresh=False: (True, "ok")
real_offer = app.stream_offer
app.stream_offer = lambda cfg: (True, cfg.get("_host", False), real_offer(cfg)[2]) if cfg.get("stream_backend") == "own" else real_offer(cfg)
opened, spawned = [], []
app.webbrowser.open = lambda url: opened.append(url) or True
real_exec = asyncio.create_subprocess_exec
async def spy(*a, **k):
    spawned.append(a); return await real_exec(*a, **k)
app.asyncio.create_subprocess_exec = spy

def mk(backend="own", host=False, pid=""):
    cfg = json.loads(json.dumps(app.DEFAULT_CFG))
    cfg.update(server_port=PORT, stream_backend=backend, parsec_peer_id=pid, _host=host)
    cfg["paths"] = {k: str(fake) for k in app.EMUS}
    cfg["games"] = [{"name": "Urban Reign", "system": "ps2", "rom": str(iso)}]
    log = []
    return cfg, log, app.Engine(lambda c=cfg: c, lambda s, m, clip=None: log.append((s, m, clip)))

ch, lh, eh = mk(host=True); cg, lg, eg = mk(host=False)
eh.toggle(); time.sleep(0.5); eg.toggle(); time.sleep(2.0)
check("matched on the built-in stream", any("built-in stream" in m for _, m, _ in lh) and any("built-in stream" in m for _, m, _ in lg),
      str([m for _, m, _ in lh][-3:]))
check("guest opened exactly one browser link", len(opened) == 1 and opened[0].startswith(f"http://127.0.0.1:{PORT + 1}/play?t="), str(opened))
st = urllib.request.urlopen(opened[0], timeout=5)
check("that link serves the guest page", st.status == 200 and b"RTCPeerConnection" in st.read())
emu = [a for a in spawned if str(fake) in a[0]]
sh = [a for a in spawned if any("stream_host.py" in str(x) for x in a)]
check("host started the emulator for the stream", len(emu) == 1 and emu[0][1:3] == ("-batch", "-fullscreen"), str(emu))
check("host started stream_host with server + key + slot", len(sh) == 1 and "--server" in sh[0] and f"http://127.0.0.1:{PORT + 1}" in sh[0] and "--key" in sh[0], str(sh))
key = sh[0][sh[0].index("--key") + 1]
cfgj = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT + 1}/cfg?t={key}", timeout=5))
check("host's key is the host role", cfgj["role"] == "host" and cfgj["game"] == "ps2:urban-reign", str(cfgj))
check("guest's link key is the guest role", json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT + 1}/cfg?t=" + opened[0].split("t=")[1], timeout=5))["role"] == "guest")
check("host message mentions picking the window", any("pick the game" in m for _, m, _ in lh))
time.sleep(3)   # stream_host exits here (no GStreamer in the sandbox) -> session should wind down cleanly
check("host session winds down when the stream process ends", lh[-1][0] == "idle" and "Session ended" in lh[-1][1], lh[-1][1])
eh.stop(); eg.stop()

# two guests that can't host never pair; a Parsec user never pairs with a built-in user
opened.clear(); spawned.clear()
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
print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
