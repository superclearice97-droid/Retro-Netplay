"""The real stream host (stream_host.py with GStreamer's WebRTC) streaming to the real friend's page in Chromium.
A moving test picture stands in for the game window. Checks: the page gets video frames, and the controller
channel works (the host assigns Player 2 over it). Needs GStreamer with webrtcbin, Python GObject and Playwright;
skips otherwise (the automatic build installs them and runs it on every change)."""
import json, os, socket, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "server.py")
HOST = os.path.join(HERE, "..", "stream_host.py")
try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    assert Gst.ElementFactory.find("webrtcbin") and Gst.ElementFactory.find("x264enc")
    from playwright.sync_api import sync_playwright
except Exception as e:  # noqa: BLE001
    print(f"SKIP (needs GStreamer webrtcbin + x264enc, PyGObject and Playwright: {e})")
    sys.exit(0)

ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""), flush=True)
    if not cond and os.environ.get("GITHUB_ACTIONS"):
        text = str(extra).replace("%", "%25").replace("\r", "").replace("\n", "%0A")[-6000:]
        print(f"::error title=real stream test::{name}%0A{text}", flush=True)


# ---- the quick encoder test that decides whether to fall back to software
sys.path.insert(0, os.path.join(HERE, ".."))
import stream_host as S
_, x264 = S.pick_encoder({"x264enc"}, "x264enc")
works, why = S.encoder_works("x264enc", x264, "constrained-baseline")
check("encoder test: a working encoder passes", works, why)
works, why = S.encoder_works("x264enc", x264, "no-such-profile")
check("encoder test: an encoder that can't make the stream fails, with a reason", not works and why, why)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def search(port, client, can_host):
    s = socket.create_connection(("127.0.0.1", port))
    req = {"v": 1, "op": "search", "game": "ps2:urban-reign", "lanes": ["stream"], "client": client,
           "can_host": {"stream": can_host}, "info": {"stream": {"backend": "own"}}}
    s.sendall((json.dumps(req) + "\n").encode())
    return s


def read_msg(s, timeout=10):
    s.settimeout(timeout); buf = b""
    while not buf.endswith(b"\n"):
        buf += s.recv(4096)
    return json.loads(buf)


port, hport = free_port(), free_port()
srv = subprocess.Popen([sys.executable, "-u", SERVER, str(port), str(hport)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.STDOUT, env={**os.environ, "NETPLAY_DATA": tempfile.mkdtemp(), "NETPLAY_ICE_JSON": "[]"})
time.sleep(1.0)
base = f"http://127.0.0.1:{hport}"
hostlog = tempfile.mktemp(suffix=".log")
host = None
try:
    a = search(port, "desktop-host", True); time.sleep(0.2); b = search(port, "odin-guest", False)
    ma, mb = read_msg(a), read_msg(b)
    hk = ma["session"]["key"]
    host = subprocess.Popen([sys.executable, "-u", HOST, "--server", base, f"--key={hk}", "--capture", "test",
                             "--no-audio", "--encoder", "x264enc", "--fps", "30"],
                            stdout=open(hostlog, "w"), stderr=subprocess.STDOUT, env={**os.environ, "HOME": tempfile.mkdtemp()})
    time.sleep(1.5)
    check("stream host is running (virtual controller and GStreamer ready)", host.poll() is None, open(hostlog).read())
    chrome = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    with sync_playwright() as p:
        kw = {"executable_path": chrome} if os.path.exists(chrome) else {}
        br = p.chromium.launch(headless=True, args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required",
                                                     "--disable-features=WebRtcHideLocalIpsWithMdns"], **kw)
        page = br.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + mb["session"]["play"])
        got = False
        for _ in range(60):
            time.sleep(0.5)
            if page.evaluate("document.getElementById('v').videoWidth") > 0:
                got = True
                break
            if host.poll() is not None:
                break
        log = open(hostlog).read()
        check("the friend's page gets video from the real stream host", got,
              page.evaluate("document.getElementById('mt').textContent") + " | host log:\n" + log[-5000:])
        if got:
            time.sleep(1.5)
            check("the controller channel works (host assigned Player 2 over it)",
                  page.evaluate("window.__netplay.slot") == 2, log[-800:])
            # WebRTC may start smaller and scale up, so only check it's a real picture
            w = page.evaluate("document.getElementById('v').videoWidth")
            check("the video is a real picture (not 0 or 2 pixels)", w >= 160, w)
            page.keyboard.down("z"); time.sleep(0.3); page.keyboard.up("z"); time.sleep(0.3)
            check("button presses are sent over it", page.evaluate("window.__netplay.packets") > 5)
        check("no errors on the page", not errors, "; ".join(errors))
        check("host reached 'connected'", "connection: connected" in open(hostlog).read(), open(hostlog).read()[-800:])
        br.close()
finally:
    if host and host.poll() is None:
        host.terminate()
    srv.terminate()
print("ALL PASSED" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
