"""End-to-end check of the streaming path using real WebRTC inside Chromium:
real matchmaker server -> session keys -> real web/guest.html <-> simulated host page (sim_host.js)."""
import json, os, socket, subprocess, sys, time, urllib.request, urllib.error
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "server.py")
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def search(port, client, can_host, backend="own"):
    s = socket.create_connection(("127.0.0.1", port))
    req = {"v": 1, "op": "search", "game": "ps2:urban-reign", "lanes": ["stream"], "client": client,
           "can_host": {"stream": can_host}, "info": {"stream": {"backend": backend} if backend else {}}}
    s.sendall((json.dumps(req) + "\n").encode())
    return s


def read_msg(s, timeout=5):
    s.settimeout(timeout); buf = b""
    while not buf.endswith(b"\n"):
        buf += s.recv(4096)
    return json.loads(buf)


def http(url, data=None, method=None):
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


port, hport = free_port(), free_port()
srv = subprocess.Popen([sys.executable, "-u", SERVER, str(port), str(hport)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(1.0)
base = f"http://127.0.0.1:{hport}"
try:
    # ---- matchmaking hands both sides their own key
    a = search(port, "desktop-host", True)
    time.sleep(0.2)
    b = search(port, "browser-guest", False)
    ma, mb = read_msg(a), read_msg(b)
    check("matched on stream lane", ma["lane"] == "stream" and ma["role"] == "host" and mb["role"] == "guest")
    hk, gk = ma["session"]["key"], mb["session"]["key"]
    check("each side has a different key", hk != gk and len(hk) >= 20)
    check("guest gets a play path", mb["session"]["play"] == f"/play?t={gk}" and "play" not in ma["session"])

    # ---- a client that did NOT ask for the built-in streamer gets no session (old behaviour)
    c = search(port, "parsec-a", True, backend="parsec"); time.sleep(0.2)
    d = search(port, "parsec-b", False, backend="parsec")
    mc = read_msg(c)
    check("non-own backends get no session", "session" not in mc and "session" not in read_msg(d))

    # ---- access control
    check("bad key -> 404 on /sig", http(f"{base}/sig?t=nope")[0] == 404)
    check("host key cannot open the guest page", http(f"{base}/play?t={hk}")[0] == 404)
    check("guest page needs the guest key", http(f"{base}/play?t={gk}")[0] == 200)
    st, body = http(f"{base}/play?t={gk}")
    check("guest page loads nothing from other sites", b"http://" not in body and b"https://" not in body)
    check("garbage body -> 400", http(f"{base}/sig?t={hk}", b"not json", "POST")[0] == 400)
    check("array body -> 400", http(f"{base}/sig?t={hk}", b"[1,2]", "POST")[0] == 400)
    check("oversize body -> 413", http(f"{base}/sig?t={hk}", b"x" * 20000, "POST")[0] == 413)
    # roles: a message posted with the guest key is only readable with the host key
    http(f"{base}/sig?t={gk}", json.dumps({"type": "hello"}).encode(), "POST")
    st, body = http(f"{base}/sig?t={gk}&wait=0")
    check("sender does not read its own message", json.loads(body)["msgs"] == [])
    st, body = http(f"{base}/sig?t={hk}&wait=0")
    check("other side receives it", [m["type"] for m in json.loads(body)["msgs"]] == ["hello"])

    # ---- the real thing: WebRTC between guest.html and the simulated host
    a2 = search(port, "host2", True); time.sleep(0.2); b2 = search(port, "guest2", False)
    ma, mb = read_msg(a2), read_msg(b2)
    hk, gk = ma["session"]["key"], mb["session"]["key"]
    with open(os.path.join(HERE, "sim_host.js")) as f:
        sim_js = f.read()
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=CHROME, headless=True, args=[
            "--no-sandbox", "--autoplay-policy=no-user-gesture-required",
            "--disable-features=WebRtcHideLocalIpsWithMdns", "--use-fake-ui-for-media-stream"])
        ctx = br.new_context(bypass_csp=True)
        host = ctx.new_page(); host.goto(base + "/")
        host.evaluate(f"({sim_js})('{hk}')")
        guest = ctx.new_page()
        errors = []
        guest.on("pageerror", lambda e: errors.append(str(e)))
        guest.goto(f"{base}{mb['session']['play']}")
        try:
            guest.wait_for_function("window.__netplay.pc && window.__netplay.pc.connectionState === 'connected'", timeout=15000)
            connected = True
        except Exception:
            connected = False
        check("WebRTC connected", connected)
        try:
            guest.wait_for_function("document.getElementById('v').videoWidth > 0", timeout=10000)
            got_video = True
        except Exception:
            got_video = False
        check("video frames arrive", got_video, guest.evaluate("document.getElementById('v').videoWidth + 'x' + document.getElementById('v').videoHeight"))
        time.sleep(3)
        stats = guest.evaluate("window.__netplay.stats")
        check("fps measured", (stats.get("fps") or 0) > 5, str(stats.get("fps")))
        check("rtt measured over data channel", stats.get("rttMs") is not None, str(stats.get("rttMs")))
        check("jitter buffer + decode measured", stats.get("jitterBufMs") is not None and stats.get("decodeMs") is not None,
              f"jb={stats.get('jitterBufMs')} dec={stats.get('decodeMs')} added={stats.get('addedMs')}")
        check("slot assignment shown", guest.evaluate("window.__netplay.slot") == 2)
        hostlog = host.evaluate("window.simLog")
        check("host got guest stats", any(m.get("t") == "stats" for m in hostlog["ctl"]))
        check("host saw exactly one offer", hostlog["offers"] == 1, str(hostlog["offers"]))

        # ---- input: keyboard on the guest page -> binary packet on the host
        guest.bring_to_front(); guest.keyboard.down("z"); time.sleep(0.4)
        guest.keyboard.down("ArrowLeft"); time.sleep(0.3); guest.keyboard.up("z"); guest.keyboard.up("ArrowLeft"); time.sleep(0.4)
        pads = host.evaluate("window.simLog.pads")
        check("input packets arrive", len(pads) > 5, str(len(pads)))
        check("packet format is v1", all(p["ver"] == 1 for p in pads))
        check("button A seen", any(p["buttons"] & 1 for p in pads))
        check("d-pad left + A together seen", any(p["buttons"] & 1 and p["buttons"] & (1 << 14) for p in pads))
        check("released state seen last", pads[-1]["buttons"] == 0)
        seqs = [p["seq"] for p in pads]
        check("sequence numbers increase by one (loopback, no loss)", all(((y - x) & 255) == 1 for x, y in zip(seqs, seqs[1:])))
        check("no page errors", not errors, "; ".join(errors))
        # ---- hud toggle
        guest.evaluate("document.getElementById('pill').click()"); time.sleep(1.3)
        hud = guest.evaluate("document.getElementById('hud').textContent")
        check("HUD shows the host's timing too", "encode 2.5" in hud and "estimated total delay" in hud, hud.replace("\n", " | ")[-170:])
        check("HUD shows numbers", "round trip" in hud and "fps" in hud, hud.replace("\n", " | ")[:140])
        br.close()
finally:
    srv.terminate()
print("ALL PASSED" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
