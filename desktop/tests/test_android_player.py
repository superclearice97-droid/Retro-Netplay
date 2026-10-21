"""The phone app's own stream player (PlayerActivity: libwebrtc + Android's video decoder) in an Android emulator,
against the REAL stream host on this computer (GStreamer test picture -> x264 -> WebRTC) over the matchmaker's TCP
stream connection. No browser, no web page. Checks: video frames are decoded, the on-screen picture starts, the
pretend controller's presses arrive at the host, the host makes the phone Player 2, the phone leaves politely, and a
wrong key is explained. The emulator reaches this computer at 10.0.2.2.
Needs: adb with one emulator running, the app and its test APK installed (CI: the android-player job), GStreamer
webrtcbin + x264enc. Skips otherwise."""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "..")
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    assert Gst.ElementFactory.find("webrtcbin") and Gst.ElementFactory.find("x264enc")
    assert shutil.which("adb") and "\tdevice" in subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
except Exception as e:  # noqa: BLE001
    print(f"SKIP (needs an Android emulator on adb and GStreamer webrtcbin + x264enc: {e!r})")
    sys.exit(0)

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(extra)[-3000:]}]" if extra and not cond else ""), flush=True)

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def search(port, game, can_host):
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall((json.dumps({"v": 1, "op": "search", "game": game, "lanes": ["stream"], "client": "test",
                           "can_host": {"stream": can_host}, "info": {"stream": {"backend": "own", "sig": "tcp"}}}) + "\n").encode())
    return s

def read(s, timeout=10):
    s.settimeout(timeout); f = s.makefile("rb")
    while True:
        m = json.loads(f.readline())
        if m.get("op") != "ping": return m

port = free_port()
srv = subprocess.Popen([sys.executable, "-u", os.path.join(D, "server.py"), str(port), "0"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.STDOUT, env={**os.environ, "NETPLAY_DATA": tempfile.mkdtemp(), "NETPLAY_ICE_JSON": "[]"})
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", port), 0.5).close(); break
    except OSError:
        time.sleep(0.1)
procs = [srv]

def play(key, seconds=8):
    """Run the player in the emulator; -> (result dict, raw output)."""
    subprocess.run(["adb", "logcat", "-c"], capture_output=True)
    r = subprocess.run(["adb", "shell", "am", "instrument", "-w", "-e", "host", "10.0.2.2", "-e", "port", str(port),
                        "-e", "key", key, "-e", "seconds", str(seconds), "net.netplay.matchmaker.test/net.netplay.app.PlayerCheck"],
                       capture_output=True, text=True, timeout=200)
    m = re.search(r"result=(\{.*\})", r.stdout)
    res = json.loads(m.group(1)) if m else {}
    log = subprocess.run(["adb", "logcat", "-d", "-s", "NetplayPlayer:*", "org.webrtc.Logging:W", "AndroidRuntime:E"],
                         capture_output=True, text=True).stdout
    return res, r.stdout + "\n--- logcat:\n" + log[-5000:]

try:
    # ---- the real stream, with the test's controller presses
    h = search(port, "ps2:urban-reign", True); time.sleep(0.2); g = search(port, "ps2:urban-reign", False)
    hk, gk = read(h)["session"]["key"], read(g)["session"]["key"]
    hlog, pads = tempfile.mktemp(suffix=".log"), tempfile.mktemp(suffix=".jsonl")
    hp = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "recording_host.py"), "--mm", f"127.0.0.1:{port}", f"--key={hk}",
                           "--capture", "test", "--no-audio", "--encoder", "x264enc", "--fps", "30"],
                          stdout=open(hlog, "w"), stderr=subprocess.STDOUT,
                          env={**os.environ, "NETPLAY_PAD_LOG": pads, "HOME": tempfile.mkdtemp()})
    procs.append(hp)
    time.sleep(1.0)
    res, raw = play(gk)
    print("player result:", json.dumps(res), flush=True)
    check("the phone's player showed the video", res.get("playing") is True, raw + "\n--- host:\n" + open(hlog).read()[-3000:])
    check("frames were decoded (about 30 a second)", res.get("frames", 0) >= 120, res)
    check("decoded by Android's own video decoder (MediaCodec)", "MediaCodec" in str(res.get("decoder", "")) or
          "c2." in str(res.get("decoder", "")) or "OMX." in str(res.get("decoder", "")), res.get("decoder"))
    check("the host made the phone Player 2", res.get("slot") == 2, res)
    check("the player left on purpose", res.get("ended") == "You left the stream." and res.get("error") is False, res)
    check("the host's timing reached the phone", isinstance(res.get("host", {}).get("totalMs"), (int, float)), res.get("host"))
    time.sleep(1.5)
    hl = open(hlog).read()
    check("the host saw the phone leave", "ending: guest left" in hl, hl[-1500:])
    seen = [json.loads(l) for l in open(pads)] if os.path.exists(pads) else []
    presses = [s["buttons"] for s in seen]
    check("the phone's button presses arrive at the host (A down, A up, ...)", presses.count(1) >= 3 and 0 in presses, presses[:20])
    check("every press was for Player 2's pad", seen and all(s["pad"].endswith("(Player 2)") for s in seen), seen[:3])

    # ---- a wrong key is explained, no video
    res, raw = play("this-is-not-a-real-key", seconds=2)
    check("a wrong key: the player says so in words", res.get("playing") is False and "stream" in str(res.get("ended", "")).lower()
          and res.get("error") is True, raw)
finally:
    for p in procs:
        p.terminate()
print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
