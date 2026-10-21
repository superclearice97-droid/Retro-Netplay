"""The native player (stream_guest.py, headless: pretend screen, frames to appsink) against the REAL stream host
(stream_host.py: GStreamer test picture -> x264 -> WebRTC) over the matchmaker's TCP stream connection. No browser.
Checks: video frames arrive at about 60 fps, controller packets from a pretend SDL controller arrive intact at the
host, the stats file is written, a friend may pick their player slot, a wrong key is refused, the host's real error
is shown, and old and new apps aren't paired. The host's virtual controller is a recorder (tests/recording_host.py),
so this runs without /dev/uinput. Skips without GStreamer webrtcbin + x264enc + a decoder, or PySDL2."""
import json, os, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "..")
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    assert Gst.ElementFactory.find("webrtcbin") and Gst.ElementFactory.find("x264enc") and Gst.ElementFactory.find("avdec_h264")
    import sdl2, sdl2.sdlttf  # noqa: F401
except Exception as e:  # noqa: BLE001
    print(f"SKIP (needs GStreamer webrtcbin + x264enc + avdec_h264, PyGObject and PySDL2: {e})")
    sys.exit(0)

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(extra)[-3000:]}]" if extra and not cond else ""), flush=True)
    if not cond and os.environ.get("GITHUB_ACTIONS"):
        text = str(extra).replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-6000:]
        print(f"::error title=native player test::{name}%0A{text}", flush=True)

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def search(port, game, can_host, sig="tcp"):
    st = {"backend": "own", **({"sig": sig} if sig else {})}
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall((json.dumps({"v": 1, "op": "search", "game": game, "lanes": ["stream"], "client": "test",
                           "can_host": {"stream": can_host}, "info": {"stream": st}}) + "\n").encode())
    return s

def read(s, timeout=10):
    s.settimeout(timeout); b = b""
    while True:
        while not b.endswith(b"\n"):
            d = s.recv(4096)
            if not d: return None
            b += d
        m = json.loads(b.split(b"\n")[-2]); b = b""
        if m.get("op") != "ping": return m

def pair(port, game):
    h = search(port, game, True); time.sleep(0.2); g = search(port, game, False)
    return read(h)["session"]["key"], read(g)["session"]["key"]

port = free_port()
srv = subprocess.Popen([sys.executable, "-u", os.path.join(D, "server.py"), str(port), "0"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.STDOUT, env={**os.environ, "NETPLAY_DATA": tempfile.mkdtemp(), "NETPLAY_ICE_JSON": "[]"})
for _ in range(100):                 # until the matchmaker answers (a busy machine can take a few seconds)
    try:
        socket.create_connection(("127.0.0.1", port), 0.5).close(); break
    except OSError:
        time.sleep(0.1)
MM = f"127.0.0.1:{port}"
procs = []

def host(key, *extra, env=None):
    log = tempfile.mktemp(suffix=".log"); pads = tempfile.mktemp(suffix=".jsonl")
    p = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "recording_host.py"), "--mm", MM, f"--key={key}",
                          "--capture", "test", "--no-audio", "--encoder", "x264enc", "--fps", "60", *extra],
                         stdout=open(log, "w"), stderr=subprocess.STDOUT,
                         env={**(env or os.environ), "NETPLAY_PAD_LOG": pads, "HOME": tempfile.mkdtemp()})
    procs.append(p)
    return p, log, pads

def guest(key, *extra, timeout=60):
    stats = os.path.join(tempfile.mkdtemp(), "stream-stats.csv")
    p = subprocess.Popen([sys.executable, "-u", os.path.join(D, "stream_guest.py"), "--mm", MM, f"--key={key}", "--headless",
                          "--stats-file", stats, *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         env={**os.environ, "HOME": tempfile.mkdtemp()})
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
        out += "\n(player TIMED OUT)"
    return subprocess.CompletedProcess(p.args, p.returncode, out, err), stats

try:
    # ---- 1. the real stream, with a pretend controller
    hk, gk = pair(port, "ps2:urban-reign")
    hp, hlog, pads = host(hk)
    time.sleep(1.0)
    r, stats_file = guest(gk, "--seconds", "6", "--test-input")
    out = r.stdout
    stats = [json.loads(l[6:]) for l in out.splitlines() if l.startswith("STATS ")]
    fps = [s.get("fps") or 0 for s in stats]
    check("the player connected and showed video", any(l.startswith("[player] video:") for l in out.splitlines()), out + r.stderr)
    check("frames arrive at about 60 fps", stats and max(fps) >= 55 and sorted(fps)[len(fps) // 2] >= 50, fps)
    check("the picture is the host's full size (640x480)", stats and stats[-1]["w"] == 640 and stats[-1]["h"] == 480, stats[-1:])
    check("decode time and round trip are measured", stats and stats[-1]["decodeMs"] is not None and stats[-1]["rttMs"] is not None, stats[-1:])
    check("the host made this player Player 2", stats and stats[-1]["slot"] == 2)
    check("the player left cleanly", r.returncode == 0 and out.rstrip().endswith("GUEST ENDED: you left"), out[-500:])
    time.sleep(1.0)
    hl = open(hlog).read()
    check("the host saw the guest leave", "ending: guest left" in hl, hl[-1500:])
    seen = [json.loads(l) for l in open(pads)] if os.path.exists(pads) else []
    states = [(s["buttons"], tuple(s["ax"]), s["lt"], s["rt"]) for s in seen]
    want = [(1, (0, 0, 0, 0), 0, 0),                                  # A
            (1 | 1 << 14, (0, 0, 0, 0), 0, 0),                        # A + d-pad left
            (1 << 9 | 1 << 13, (16384, -32767, 0, 0), 0, 255),        # Start + d-pad down, stick, right trigger
            (0, (0, 0, 0, 0), 0, 0)]                                  # let go
    pos = 0
    for st in states:
        if pos < len(want) and st == want[pos]:
            pos += 1
    check("controller packets arrive intact, in order (A, A+left, Start+down+stick+trigger, release)", pos == len(want), states)
    check("nothing else was pressed on the host", all(st in want for st in states), [st for st in states if st not in want])
    check("every host pad change came from a valid v1 packet", all(s["pad"].endswith("(Player 2)") for s in seen))
    rows = open(stats_file).read().splitlines() if os.path.exists(stats_file) else []
    check("the player wrote its numbers to stream-stats.csv", len(rows) >= 2 and rows[0].startswith("time,rttMs,jitterBufMs,decodeMs"), rows[:2])

    # ---- 2. a friend may choose their player
    hk, gk = pair(port, "ps2:slot")
    hp, hlog, pads = host(hk, "--slot-pick")
    time.sleep(1.0)
    r, _ = guest(gk, "--seconds", "2", "--want-slot", "3")
    stats = [json.loads(l[6:]) for l in r.stdout.splitlines() if l.startswith("STATS ")]
    check("asking for Player 3 makes the host switch to Player 3", stats and stats[-1]["slot"] == 3, r.stdout[-1500:] + r.stderr[-1500:] + open(hlog).read()[-1500:])
    time.sleep(0.5)
    check("...and the host now drives Player 3's pad", "guest switched to Player 3" in open(hlog).read(), open(hlog).read()[-800:])

    # ---- 3. a wrong key
    r, _ = guest("this-is-not-a-real-key", timeout=30)
    check("a wrong key is refused with a plain message", r.returncode == 1 and "This stream has ended" in r.stdout, r.stdout[-500:])

    # ---- 4. the host's real error reaches the player
    hk, gk = pair(port, "ps2:broken")
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    hp, hlog, pads = host(hk, "--capture", "x11", env=env)       # (later --capture wins: screen capture with no screen)
    time.sleep(1.0)
    r, _ = guest(gk, timeout=40)
    last = r.stdout.rstrip().splitlines()[-1] if r.stdout.strip() else ""
    check("the player shows the host's own error message", r.returncode == 1 and
          last.startswith("GUEST ENDED: The stream stopped on your friend's computer: capturing or encoding the game failed")
          and "X display" in last, r.stdout[-800:])

    # ---- 5. old and new apps
    old = search(port, "ps2:mixed", True, sig=None); time.sleep(0.2)
    new = search(port, "ps2:mixed", False)
    try:
        m = read(new, 2)
    except socket.timeout:
        m = None
    check("an old host and a new player are never paired into a session that can't work", m is None, m)
finally:
    for p in procs:
        if p.poll() is None:
            p.terminate()
    srv.terminate()
print("ALL PASSED" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
