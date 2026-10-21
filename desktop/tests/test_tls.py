"""Encrypted matchmaker connections (starttls on the TCP port) with a real certificate from a throwaway authority.
Checks: the app's helpers switch to TLS and verify the certificate; searches, the stream connection, the friends
connection, the invite-code lookup and the app's own search all work over it; old plain clients still work on the
same matchmaker; a certificate from someone else is refused; a matchmaker that stops offering TLS is refused once it
was seen with it; a matchmaker without a certificate says so and the connection carries on in plain text."""
import asyncio, json, os, shutil, socket, ssl, subprocess, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
home = tempfile.mkdtemp(); os.environ["HOME"] = home
if not shutil.which("openssl"):
    print("SKIP (needs the openssl command to make a test certificate)"); sys.exit(0)
import mmtls, stream_signal, invites, social

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""), flush=True)

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

d = tempfile.mkdtemp()
def sh(*a):
    subprocess.run(a, cwd=d, check=True, capture_output=True)
def make_ca(name):
    sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", f"/CN={name}",
       "-keyout", f"{name}.key", "-out", f"{name}.crt")
make_ca("ca"); make_ca("other-ca")
open(os.path.join(d, "san.ext"), "w").write("subjectAltName=DNS:localhost\n")
sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost", "-keyout", "mm.key", "-out", "mm.csr")
sh("openssl", "x509", "-req", "-in", "mm.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-days", "2",
   "-extfile", "san.ext", "-out", "mm.crt")

def start_server(tls):
    port = free_port()
    env = {**os.environ, "NETPLAY_DATA": tempfile.mkdtemp(), "NETPLAY_ICE_JSON": "[]"}
    if tls:
        env.update(NETPLAY_TLS_CERT=os.path.join(d, "mm.crt"), NETPLAY_TLS_KEY=os.path.join(d, "mm.key"))
    p = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "..", "server.py"), str(port), "0"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close(); break
        except OSError:
            time.sleep(0.1)
    return p, port

def lines(sock, n=1, timeout=8):
    sock.settimeout(timeout); f = sock.makefile("rb"); out = []
    while len(out) < n:
        m = json.loads(f.readline())
        if m.get("op") != "ping": out.append(m)
    return out

def search(conn, can_host):
    conn.sendall((json.dumps({"v": 1, "op": "search", "game": "ps2:urban-reign", "lanes": ["stream"], "client": "t",
                              "can_host": {"stream": can_host}, "info": {"stream": {"backend": "own", "sig": "tcp"}}}) + "\n").encode())

mmtls.wants_tls = lambda h: h == "localhost"      # (in real use: internet names only; the test certificate is for localhost)
mmtls.CA_FILE = os.path.join(d, "ca.crt")
procs = []
try:
    srv, port = start_server(tls=True); procs.append(srv)

    # ---- the helper
    c = mmtls.connect("localhost", port)
    check("the app switches to TLS when the matchmaker offers it", isinstance(c, ssl.SSLSocket) and c.version() in ("TLSv1.2", "TLSv1.3"),
          type(c))
    c.sendall(b'{"v":1,"op":"whoami"}\n')
    w = lines(c)[0]
    check("...and the matchmaker answers normally over it", w.get("op") == "whoami", w)
    check("the matchmaker is remembered as encrypted", "localhost" in json.loads(mmtls.SEEN.read_text()))

    # ---- searches, then the stream connection, all encrypted
    h = mmtls.connect("localhost", port); search(h, True); time.sleep(0.2)
    g = mmtls.connect("localhost", port); search(g, False)
    mh, mg = lines(h)[0], lines(g)[0]
    check("two apps are matched over TLS", mh.get("op") == "matched" and mg.get("op") == "matched", (mh, mg))
    got, A = [], stream_signal.TcpSignaling("localhost", port, mh["session"]["key"])
    B = stream_signal.TcpSignaling("localhost", port, mg["session"]["key"], on_msg=got.append)
    check("the stream connection works over TLS", A.connect()["role"] == "host" and B.connect()["role"] == "guest"
          and isinstance(A.sock, ssl.SSLSocket))
    A.start(); B.start(); A.send({"type": "offer", "sdp": "v=0 secret fingerprint"}); time.sleep(0.5)
    check("...and relays the setup messages", got == [{"type": "offer", "sdp": "v=0 secret fingerprint"}], got)
    A.stop(); B.stop()

    # ---- the friends connection and the invite-code lookup
    events = []
    P = social.Presence("localhost", port, "a" * 32, "Sam", [], lambda ev, d: events.append((ev, d)))
    P.start()
    for _ in range(50):
        if any(ev == "up" for ev, _ in events): break
        time.sleep(0.1)
    check("the friends connection works over TLS", any(ev == "up" for ev, _ in events), events[:3])
    check("...and it is encrypted", isinstance(P.sock, ssl.SSLSocket), type(P.sock))
    P.stop()
    inv = mmtls.connect("localhost", port)
    inv.sendall((json.dumps({"v": 1, "op": "search", "game": "gba:sonic-battle", "lanes": ["native"], "client": "t",
                             "invite": "ABCD2345"}) + "\n").encode()); time.sleep(0.3)
    check("an invite code is looked up over TLS", invites.lookup_code("localhost", port, "ABCD2345") == "gba:sonic-battle")
    inv.close()

    # ---- the app's own search (asyncio) over TLS
    import app
    cfg = json.loads(json.dumps(app.DEFAULT_CFG))
    cfg.update(server_host="localhost", server_port=port, stream_backend="own")
    cfg["games"] = [{"name": "Urban Reign", "system": "ps2", "rom": ""}]
    cfg["invite"]["enabled"] = False
    app.own_host_ready = lambda refresh=False: (False, "no")
    app.stream_offer = lambda c: (True, False, {"backend": "own", "sig": "tcp"})
    app.readiness = lambda c, g: (["stream"], "")
    msgs = []
    E = app.Engine(lambda: cfg, lambda s, m, clip=None: msgs.append((s, m)))
    async def no_play(*a, **k): msgs.append(("played", "")); return None
    E.play = no_play
    other = mmtls.connect("localhost", port); search(other, True); time.sleep(0.2)
    E.toggle()
    for _ in range(80):
        if any(s == "played" for s, _ in msgs): break
        time.sleep(0.1)
    check("the app's own search is matched over TLS", any(s == "played" for s, _ in msgs), msgs[-3:])
    other.close()

    # ---- old apps (plain text) still work on the same matchmaker
    o1 = socket.create_connection(("127.0.0.1", port)); search(o1, True); time.sleep(0.2)
    o2 = socket.create_connection(("127.0.0.1", port)); search(o2, False)
    check("old plain-text apps are still matched", lines(o1)[0].get("op") == "matched" and lines(o2)[0].get("op") == "matched")

    # ---- a certificate from someone else
    mmtls.CA_FILE = os.path.join(d, "other-ca.crt")
    try:
        mmtls.connect("localhost", port); e = None
    except mmtls.TlsError as x:
        e = x
    check("a certificate the app can't verify is refused, with a plain reason", e and "certificate" in str(e), e)
    try:
        stream_signal.TcpSignaling("localhost", port, "k" * 24).connect(); e = None
    except stream_signal.SignalError as x:
        e = x
    check("...also for the stream connection (why = tls)", e and e.why == "tls", e)
    mmtls.CA_FILE = os.path.join(d, "ca.crt")

    # ---- the same name, now without TLS: refused (it was seen encrypted before)
    plain, pport = start_server(tls=False); procs.append(plain)
    try:
        mmtls.connect("localhost", pport); e = None
    except mmtls.TlsError as x:
        e = x
    check("a matchmaker that stops offering TLS is refused", e and "used to be encrypted" in str(e), e)
    # ...but one never seen with TLS carries on in plain text on the same connection
    mmtls.SEEN.unlink()
    c = mmtls.connect("localhost", pport)
    c.sendall(b'{"v":1,"op":"whoami"}\n')
    check("a matchmaker without a certificate says so, and the connection carries on in plain text",
          not isinstance(c, ssl.SSLSocket) and lines(c)[0].get("op") == "whoami")

    # ---- an older matchmaker that doesn't know starttls: start over in plain text
    def old_server(s):
        for _ in range(2):
            c, _a = s.accept(); f = c.makefile("rb"); first = json.loads(f.readline())
            if first.get("op") == "starttls":
                c.sendall(b'{"op":"error","msg":"first message must be {\\"op\\":\\"search\\", ...}"}\n'); c.close(); continue
            c.sendall(b'{"op":"whoami","v":1}\n'); c.close()
    old = socket.socket(); old.bind(("127.0.0.1", 0)); old.listen(2)
    threading.Thread(target=old_server, args=(old,), daemon=True).start()
    c = mmtls.connect("localhost", old.getsockname()[1])
    c.sendall(b'{"v":1,"op":"whoami"}\n')
    check("an older matchmaker that doesn't know starttls: the app starts over in plain text", lines(c)[0].get("op") == "whoami")

    # ---- which matchmakers get TLS (the real rule)
    import importlib; importlib.reload(mmtls)
    check("internet names use TLS", mmtls.wants_tls("mm.example.net") and mmtls.wants_tls("Play.Example.Org"))
    check("addresses and home-network names don't", not any(mmtls.wants_tls(h) for h in
          ("192.168.1.20", "203.0.113.5", "::1", "[2001:db8::1]", "localhost", "gamepc", "gamepc.local", "nas.lan")))
finally:
    for p in procs:
        p.terminate()
print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
