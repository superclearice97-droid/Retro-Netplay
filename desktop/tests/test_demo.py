"""The /demo test page (real WebRTC in Chromium). (The phone app no longer opens stream pages: it has its own player,
tested in test_android_player.py; old phones and new computers are never paired, see test_android_stream.py.)"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"     # (this one if present, else Playwright's own)
CHROME = CHROME if os.path.exists(CHROME) else None
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()

# without --demo the test pages must not exist
port = free_port(); hport = port + 1
srv0 = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "desktop/server.py"), str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(1)
check("/demo is OFF by default", get(f"http://127.0.0.1:{hport}/demo")[0] == 200 and b"matchmaker is running" in get(f"http://127.0.0.1:{hport}/demo")[1]
      and get(f"http://127.0.0.1:{hport}/demo/new")[0] == 200 and b"host" not in get(f"http://127.0.0.1:{hport}/demo/new")[1])
srv0.terminate(); time.sleep(0.5)

port = free_port(); hport = port + 1
srv = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "desktop/server.py"), str(port), "--demo"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(1)
base = f"http://127.0.0.1:{hport}"
try:
    check("--demo serves the test page", get(base + "/demo")[0] == 200 and b"Host (simulated)" in get(base + "/demo")[1])
    check("--demo can make a test session", set(json.loads(get(base + "/demo/new")[1])) == {"host", "guest"})
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required", "--disable-features=WebRtcHideLocalIpsWithMdns"])
        ctx = br.new_context(bypass_csp=False, viewport={"width": 420, "height": 800}, has_touch=True)
        page = ctx.new_page(); errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(base + "/demo")
        fr = None
        for _ in range(60):
            fr = next((f for f in page.frames if f != page.main_frame), None)
            try:
                if fr and fr.evaluate("window.__netplay && window.__netplay.pc && window.__netplay.pc.connectionState") == "connected": break
            except Exception: pass
            time.sleep(0.25)
        check("standalone demo: guest frame connects", fr is not None and fr.evaluate("window.__netplay.pc.connectionState") == "connected")
        time.sleep(2)
        check("standalone demo: video plays inside the frame", fr.evaluate("document.getElementById('v').videoWidth") > 0)
        # a touch on the on-screen A button must reach the host panel
        box = fr.evaluate("(() => { const el = [...document.querySelectorAll('#pad .b')].find(e => e.textContent === 'A'); const r = el.getBoundingClientRect(); return [r.x + r.width/2, r.y + r.height/2]; })()")
        fx, fy = fr.frame_element().bounding_box()["x"], fr.frame_element().bounding_box()["y"]
        page.touchscreen.tap(fx + box[0], fy + box[1])
        page.mouse.move(fx + box[0], fy + box[1]); page.mouse.down(); time.sleep(0.4)
        lit = page.evaluate("[...document.querySelectorAll('.chip.on')].map(c => c.textContent)")
        page.mouse.up(); time.sleep(0.4)
        lit_after = page.evaluate("[...document.querySelectorAll('.chip.on')].map(c => c.textContent)")
        check("on-screen A button lights 'A' on the host side", "A" in lit, str(lit))
        check("releasing it clears it", "A" not in lit_after, str(lit_after))
        check("host panel counted input packets", page.evaluate("window.__x = document.getElementById('sticks').textContent") and "packets 0" not in page.evaluate("document.getElementById('sticks').textContent"))
        check("no page errors", not errs, "; ".join(errs))
        br.close()

finally:
    srv.terminate()
print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
