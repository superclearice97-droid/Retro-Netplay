"""The Java core (what the Android app runs) against the real Python matchmaker and a Python built-in-stream host."""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

out = tempfile.mkdtemp()
src = [os.path.join(ROOT, "android/core/src/main/java/net/netplay/core", f) for f in os.listdir(os.path.join(ROOT, "android/core/src/main/java/net/netplay/core"))]
src.append(os.path.join(ROOT, "android/core/src/test/java/net/netplay/core/StreamTest.java"))
r = subprocess.run(["javac", "-d", out] + src, capture_output=True, text=True)
check("Java core compiles", r.returncode == 0, r.stderr[:300])

port = free_port(); hport = port + 1      # the app assumes web port = TCP port + 1
srv = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "desktop/server.py"), str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(1.0)
def host_search(backend):
    c = socket.create_connection(("127.0.0.1", port))
    c.sendall((json.dumps({"v": 1, "op": "search", "game": "ps2:urban-reign", "lanes": ["stream"], "client": "desktop-linux",
                           "can_host": {"stream": True}, "info": {"stream": {"backend": backend}}}) + "\n").encode())
    return c
def rd(c):
    c.settimeout(10); buf = b""
    while not buf.endswith(b"\n"): buf += c.recv(4096)
    return json.loads(buf)
try:
    h = host_search("own"); time.sleep(0.3)
    j = subprocess.run(["java", "-cp", out, "net.netplay.core.StreamTest", str(port), "own"], capture_output=True, text=True, timeout=30)
    kv = dict(l.split("=", 1) for l in j.stdout.splitlines() if "=" in l and not l.startswith("playUrl"))
    hm = rd(h)
    check("Android core matched the desktop host on the stream lane as guest", kv.get("LANE") == "stream" and kv.get("ROLE") == "guest", j.stdout + j.stderr)
    check("Java parsed the session key", kv.get("KEYLEN") == str(len(hm["session"]["key"])), kv.get("KEYLEN"))
    check("plan is OPEN_STREAM", kv.get("PLAN") == "OPEN_STREAM")
    check("URL points at the matchmaker web port", kv.get("URL", "").startswith(f"http://127.0.0.1:{hport}/play?t="), kv.get("URL"))
    page = urllib.request.urlopen(kv["URL"], timeout=5)
    check("the URL Android would open serves the guest page", page.status == 200 and b"RTCPeerConnection" in page.read())
    check("desktop host got the host-role key", "play" not in hm["session"] and hm["peer"]["client"] == "android")
    check("IPv6 host is bracketed", "playUrl ipv6=http://[::1]:7778/play?t=x" in j.stdout, j.stdout)
    check("empty play path -> empty url", "playUrl empty=[]" in j.stdout)
    # Parsec-mode phone must not pair with a built-in host
    h2 = host_search("own"); time.sleep(0.3)
    j2 = subprocess.run(["java", "-cp", out, "net.netplay.core.StreamTest", str(port), "parsec"], capture_output=True, text=True, timeout=30)
    check("Parsec-mode phone does not pair with a built-in host", "NOMATCH" in j2.stdout, j2.stdout + j2.stderr)
finally:
    srv.terminate()
print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
