"""Stream setup over the matchmaker's TCP connection (no web page): the real matchmaker, real sockets.
Checks: new apps pair and both get TCP sessions; each key only works for its own role and only over TCP;
messages are relayed both ways; size and rate limits; sessions expire; a reconnect replaces the old connection;
old and new apps never pair into a session that can't work; the current Android app (web page player) still joins
a new host; the relay password is short-lived and made the way the relay checks it; the client class works."""
import asyncio, base64, hashlib, hmac, json, os, socket, sys, tempfile, threading, time, urllib.request, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ["NETPLAY_DATA"] = tempfile.mkdtemp()
import server as mm, sessions, stream_signal

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""), flush=True)

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

mm.PING_SECONDS = 1          # quicker expiry checks for the test
PORT = free_port()
while True:                  # web port = PORT + 1 must be free too
    try:
        t = socket.socket(); t.bind(("127.0.0.1", PORT + 1)); t.close(); break
    except OSError:
        PORT = free_port()
threading.Thread(target=lambda: asyncio.run(mm.main(PORT, discovery=False)), daemon=True).start(); time.sleep(0.6)
WEB = f"http://127.0.0.1:{PORT + 1}"


class Line:
    def __init__(self, first):
        self.s = socket.create_connection(("127.0.0.1", PORT)); self.s.settimeout(5); self.buf = b""
        self.send(first)
    def send(self, obj):
        self.s.sendall((json.dumps(obj) + "\n").encode())
    def raw(self, data):
        self.s.sendall(data)
    def read(self, timeout=5, skip_ping=True):
        end = time.monotonic() + timeout
        while True:
            while b"\n" not in self.buf:
                left = end - time.monotonic()
                if left <= 0:
                    raise socket.timeout("timed out")
                self.s.settimeout(left)
                d = self.s.recv(65536)
                if not d:
                    return None
                self.buf += d
            line, self.buf = self.buf.split(b"\n", 1)
            m = json.loads(line)
            if not (skip_ping and m.get("op") == "ping"):
                return m
    def closed(self, timeout=5):
        try:
            while True:
                m = self.read(timeout)
                if m is None:
                    return True
        except (socket.timeout, OSError):
            return False


def search(game, can_host=True, sig="tcp", invite=None, client="desktop-linux"):
    st = {"backend": "own"}
    if sig:
        st["sig"] = sig
    req = {"v": 1, "op": "search", "game": game, "lanes": ["stream"], "client": client,
           "can_host": {"stream": can_host}, "info": {"stream": st}}
    if invite:
        req.update(invite=invite, invite_only=True)
    return Line(req)


def http(path, data=None):
    req = urllib.request.Request(WEB + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"content-type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


# ---- 1. two new apps
h = search("ps2:urban-reign", can_host=True); time.sleep(0.2)
g = search("ps2:urban-reign", can_host=False)
mh, mg = h.read(), g.read()
check("two new apps are paired on the stream lane", mh["op"] == "matched" and mg["op"] == "matched" and mh["role"] == "host", (mh, mg))
check("both sessions say sig tcp, and the guest gets no web page link",
      mh["session"].get("sig") == "tcp" and mg["session"].get("sig") == "tcp" and "play" not in mg["session"], (mh, mg))
HK, GK = mh["session"]["key"], mg["session"]["key"]
check("each side has its own key", HK != GK)

sh = Line({"v": 1, "op": "stream", "key": HK})
rh = sh.read()
check("host key opens the host's stream connection, with ICE servers", rh["op"] == "stream" and rh["role"] == "host"
      and isinstance(rh["iceServers"], list) and rh["relay"] is False and rh["game"] == "ps2:urban-reign", rh)
sg = Line({"v": 1, "op": "stream", "key": GK})
rg = sg.read()
check("guest key opens the guest's", rg["op"] == "stream" and rg["role"] == "guest", rg)

sg.send({"op": "sig", "msg": {"type": "hello"}})
check("guest -> host: hello arrives", sh.read() == {"op": "sig", "msg": {"type": "hello"}})
sh.send({"op": "sig", "msg": {"type": "offer", "sdp": "v=0 " + "x" * 9000}})
m = sg.read()
check("host -> guest: a big offer (9 KB) arrives whole", m["op"] == "sig" and m["msg"]["type"] == "offer" and len(m["msg"]["sdp"]) == 9004)
sg.send({"op": "sig", "msg": {"type": "ice", "candidate": "candidate:1 1 udp 1 1.2.3.4 5 typ host", "sdpMLineIndex": 0}})
check("guest -> host: ice arrives", sh.read()["msg"]["type"] == "ice")

# ---- 2. wrong keys
bad = Line({"v": 1, "op": "stream", "key": "not-a-real-key-at-all"})
r = bad.read()
check("a wrong key is refused", r["op"] == "error" and r["why"] == "bad_key", r)
bad2 = Line({"v": 1, "op": "stream", "key": 12345})
check("a key that isn't text is refused", bad2.read()["op"] == "error")
check("a TCP session's guest key doesn't work on the web page /cfg", http(f"/cfg?t={GK}")[0] == 404)
check("...nor its host key on /sig", http(f"/sig?t={HK}", {"type": "offer", "sdp": "x"})[0] == 404)
check("...nor does /play serve the page for it", http(f"/play?t={GK}")[0] == 404)

# ---- 3. limits
sh.send({"op": "sig", "msg": {"type": "offer", "sdp": "x" * 16500}})
r = sh.read()
check("a message over 16 KB is refused (and the connection stays)", r["op"] == "error_soft" and "large" in r["msg"], r)
sh.send({"op": "sig", "msg": "not an object"})
check("a message without a type is refused", sh.read()["op"] == "error_soft")
sh.send({"op": "sig", "msg": {"type": "status", "text": "still here"}})
check("the connection still relays after refusals", sg.read()["msg"]["text"] == "still here")
huge = Line({"v": 1, "op": "stream", "key": HK})       # (replaces sh: newest connection wins)
check("a reconnect with the same key is accepted", huge.read()["role"] == "host")
check("...and the older connection of that role is closed", sh.closed(3))
huge.raw(b'{"op":"sig","msg":{"type":"offer","sdp":"' + b"x" * 40000 + b'"}}\n')
check("a line far over the limit ends that connection", huge.closed(3))
sh = Line({"v": 1, "op": "stream", "key": HK}); sh.read()
for i in range(mm.SIG_PER_MIN + 5):
    sh.send({"op": "sig", "msg": {"type": "status", "text": str(i)}})
seen = []
while True:
    m = sh.read(3)
    if m is None or (m.get("op") == "error" and m.get("why") == "rate"):
        seen.append(m); break
check("more than the per-minute message limit ends the connection", seen and seen[-1] and seen[-1].get("why") == "rate", seen[-1:])
got = 0
try:
    while True:
        m = sg.read(1)
        if m is None: break
        got += m.get("op") == "sig"
except socket.timeout:
    pass
check("the other side only got what was sent before the limit", 0 < got <= mm.SIG_PER_MIN, got)

# ---- 4. goodbye with the host's real reason
sh = Line({"v": 1, "op": "stream", "key": HK}); sh.read()
sh.send({"op": "sig", "msg": {"type": "bye", "why": "capturing or encoding the game failed on the computer: no encoder"}})
m = sg.read()
check("the host's goodbye reaches the guest with its real reason", m["msg"] == {"type": "bye", "why": "capturing or encoding the game failed on the computer: no encoder"}, m)
check("...and then the guest's connection closes", sg.closed(3))
again = Line({"v": 1, "op": "stream", "key": GK})
check("the ended session's key no longer works", again.read()["op"] == "error")

# ---- 5. expiry
h = search("ps2:expiry"); time.sleep(0.2); g = search("ps2:expiry", can_host=False)
k = h.read()["session"]["key"]; g.read()
c = Line({"v": 1, "op": "stream", "key": k}); c.read()
sessions.by_key[k][0].created -= sessions.SESSION_TTL + 1
r = c.read(5)
check("after 2 hours the session ends with a plain message", r and r["op"] == "error" and r["why"] == "expired" and "2 hours" in r["msg"], r)
check("...and its keys stop working", Line({"v": 1, "op": "stream", "key": k}).read()["op"] == "error")

mm.recent_by_ip.clear()   # (the per-address limit of 30 connections a minute is the real one; this test opens many)
# ---- 6. old and new apps
old_h = search("ps2:mix1", sig=None, client="desktop-linux-old"); time.sleep(0.2)
new_g = search("ps2:mix1", can_host=False)
try:
    r = new_g.read(1.5)
except socket.timeout:
    r = None
check("an old host and a new guest that can't host are NOT paired", r is None, r)
old_h.s.close(); new_g.s.close()

old_h = search("ps2:mix2", sig=None, client="desktop-linux-old"); time.sleep(0.2)
new_c = search("ps2:mix2", can_host=True)
a, b = old_h.read(), new_c.read()
check("an old app and a new one that can host: the new one hosts", a["role"] == "guest" and b["role"] == "host", (a, b))
check("...the new host signals over TCP, the old guest gets its web page", b["session"].get("sig") == "tcp" and a["session"].get("play", "").startswith("/play?t="), (a, b))

old_h = search("ps2:mix3", sig=None, invite="ROOM1234"); time.sleep(0.2)
new_g = search("ps2:mix3", can_host=False, invite="ROOM1234")
a, b = old_h.read(), new_g.read()
check("in a friend's room they're told why instead of waiting forever", a["op"] == "error" and b["op"] == "error"
      and "older Retro-Netplay" in b["msg"], (a, b))

# ---- 7. the current Android app (web page player) still joins a new host
h = search("ps2:android", can_host=True); time.sleep(0.2)
phone = search("ps2:android", can_host=False, sig=None, client="android")
mh, mp = h.read(), phone.read()
check("phone pairs with the new host", mh["role"] == "host" and mp["role"] == "guest")
check("phone still gets its web page link (deprecated path)", mp["session"].get("play", "").startswith("/play?t=") and "sig" not in mp["session"], mp)
PK, HK2 = mp["session"]["key"], mh["session"]["key"]
st, cfg = http(f"/cfg?t={PK}")
check("phone's key works on /cfg", st == 200 and cfg["role"] == "guest", (st, cfg))
check("the host's key is TCP-only", http(f"/cfg?t={HK2}")[0] == 404)
hc = Line({"v": 1, "op": "stream", "key": HK2}); hc.read()
check("phone posts hello on the web port", http(f"/sig?t={PK}", {"type": "hello"})[0] == 200)
check("...the host hears it on TCP", hc.read()["msg"] == {"type": "hello"})
hc.send({"op": "sig", "msg": {"type": "offer", "sdp": "v=0"}})
time.sleep(0.2)
st, res = http(f"/sig?t={PK}&wait=2")
check("host's TCP offer reaches the phone's long-poll", st == 200 and res["msgs"] and res["msgs"][0]["type"] == "offer", res)
check("the phone's key can't open a TCP stream connection", Line({"v": 1, "op": "stream", "key": PK}).read()["op"] == "error")
check("a search line over 8 KB is still refused", Line({"v": 1, "op": "search", "game": "ps2:x", "lanes": ["stream"], "pad": "x" * 9000}).closed(3))

mm.recent_by_ip.clear()
# ---- 8. relay password (TURN REST style), checked the way coturn checks it
check("bad relay settings are ignored", not sessions.configure_turn(["http://x"], "short"))
check("relay settings are accepted", sessions.configure_turn(["turn:relay.example.net:3478?transport=udp", "turns:relay.example.net:5349"], "s3cret-s3cret-s3cret"))
h = search("ps2:relay"); time.sleep(0.2); g = search("ps2:relay", can_host=False)
k = g.read()["session"]["key"]; h.read()
r = Line({"v": 1, "op": "stream", "key": k}).read()
turn = [x for x in r["iceServers"] if "username" in x]
check("with a relay set up, the stream connection hands out relay details", r["relay"] is True and turn and turn[0]["urls"][0].startswith("turn:"), r)
user, cred = turn[0]["username"], turn[0]["credential"]
expiry = int(user.split(":")[0])
check("the relay password expires a few hours from now", time.time() + 2 * 3600 < expiry <= time.time() + sessions.TURN_TTL + 5, user)
check("password = base64(HMAC-SHA1(secret, username))",
      cred == base64.b64encode(hmac.new(b"s3cret-s3cret-s3cret", user.encode(), hashlib.sha1).digest()).decode())
check("the secret itself is never sent", "s3cret" not in json.dumps(r))
sessions.configure_turn([], "")

mm.recent_by_ip.clear()
mm.recent_by_ip.clear()
h = search("ps2:busy"); time.sleep(0.2); g = search("ps2:busy", can_host=False)
k = g.read()["session"]["key"]; h.read()
mm.recent_by_ip["127.0.0.1"] = [time.monotonic()] * mm.SEARCHES_PER_MIN     # (the server's loop clock is monotonic)
r = Line({"v": 1, "op": "stream", "key": k}).read()
check("the per-address limit applies to stream connections, and says so (not 'wrong key')",
      r["op"] == "error" and r["why"] == "busy" and "too many" in r["msg"], r)
mm.recent_by_ip.clear()
# ---- 9. the client class (what the host, the player and the app use)
h = search("ps2:client"); time.sleep(0.2); g = search("ps2:client", can_host=False)
hk, gk = h.read()["session"]["key"], g.read()["session"]["key"]
got_h, got_g, ended = [], [], []
A = stream_signal.TcpSignaling("127.0.0.1", PORT, hk, on_msg=got_h.append, on_end=lambda w: ended.append(("h", w)))
B = stream_signal.TcpSignaling("127.0.0.1", PORT, gk, on_msg=got_g.append, on_end=lambda w: ended.append(("g", w)))
check("client: connect returns the role", A.connect()["role"] == "host" and B.connect()["role"] == "guest")
A.start(); B.start()
B.send({"type": "hello"}); A.send({"type": "offer", "sdp": "v=0"})
time.sleep(0.5)
check("client: messages both ways", got_h == [{"type": "hello"}] and got_g == [{"type": "offer", "sdp": "v=0"}], (got_h, got_g))
A.send({"type": "bye", "why": "the game window was closed"}); time.sleep(0.8)
check("client: the guest gets the goodbye and the end", got_g[-1].get("why") == "the game window was closed" and ("g", "session closed") in ended, (got_g, ended))
try:
    stream_signal.TcpSignaling("127.0.0.1", PORT, "wrong-key-wrong-key").connect(); e = None
except stream_signal.SignalError as x:
    e = x
check("client: a wrong key raises a plain error", e and e.why == "bad_key", e)
try:
    stream_signal.TcpSignaling("127.0.0.1", free_port(), "k", timeout=2).connect(); e = None
except stream_signal.SignalError as x:
    e = x
check("client: no matchmaker raises 'unreachable'", e and e.why == "unreachable", e)
check("client: addresses parse", stream_signal.parse_address("1.2.3.4:7000") == ("1.2.3.4", 7000)
      and stream_signal.parse_address("[::1]:9") == ("::1", 9) and stream_signal.parse_address("host") == ("host", 7777))

print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
