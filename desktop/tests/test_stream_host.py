"""Host-side logic: packet parsing, virtual pad events, encoder choice, ICE conversion, doctor text, and the
Session orchestration driven through the REAL matchmaker server with a fake media backend."""
import json, os, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import uinput_pad as U
import stream_host as S

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)

def pkt(seq=1, buttons=0, ax=(0, 0, 0, 0), lt=0, rt=0, ver=1):
    return struct.pack("<BBI4h2B", ver, seq, buttons, *ax, lt, rt)

# ---------------------------------------------------------------- packets and pad events
check("packet is 16 bytes (matches guest.html)", U.PACKET.size == 16)
p = U.parse_packet(pkt(5, 0b1, (100, -200, 300, -400), 7, 9))
check("parse packet", p == {"seq": 5, "buttons": 1, "ax": (100, -200, 300, -400), "lt": 7, "rt": 9}, str(p))
check("reject wrong length / version / type", U.parse_packet(b"x" * 15) is None and U.parse_packet(pkt(ver=2)) is None and U.parse_packet("hi") is None)
n = U.neutral()
ev = U.diff_events(n, U.parse_packet(pkt(1, 1)))
check("A press -> BTN_SOUTH down + SYN", ev == [(U.EV_KEY, U.BTN_SOUTH, 1), (U.EV_SYN, 0, 0)], str(ev))
check("no change -> no events", U.diff_events(n, n) == [])
dp = U.parse_packet(pkt(1, (1 << 12) | (1 << 15)))     # up + right
ev = U.diff_events(n, dp)
check("d-pad up+right -> hat X=+1, hat Y=-1", (U.EV_ABS, U.ABS_HAT0X, 1) in ev and (U.EV_ABS, U.ABS_HAT0Y, -1) in ev, str(ev))
ev = U.diff_events(dp, n)
check("d-pad release -> hats back to 0", (U.EV_ABS, U.ABS_HAT0X, 0) in ev and (U.EV_ABS, U.ABS_HAT0Y, 0) in ev)
ev = U.diff_events(n, U.parse_packet(pkt(1, 0, (1000, -1000, 5, 6), 255, 128)))
check("sticks and triggers map to axes", (U.EV_ABS, U.ABS_X, 1000) in ev and (U.EV_ABS, U.ABS_Y, -1000) in ev
      and (U.EV_ABS, U.ABS_RX, 5) in ev and (U.EV_ABS, U.ABS_RY, 6) in ev and (U.EV_ABS, U.ABS_Z, 255) in ev and (U.EV_ABS, U.ABS_RZ, 128) in ev)
all_bits = sum(1 << b for b in U.BUTTON_KEYS)
ev = U.diff_events(n, U.parse_packet(pkt(1, all_bits)))
check("every mapped button yields a key event", len([e for e in ev if e[0] == U.EV_KEY]) == len(U.BUTTON_KEYS))
check("event struct is 24 bytes on 64-bit", len(U.pack_event(1, 2, 3)) == struct.calcsize("@llHHi"))
check("uinput_user_dev block size is 1116 bytes", len(U.user_dev("x")) == 80 + 8 + 4 + 4 * 64 * 4)
blk = U.user_dev("Netplay Virtual Pad")
vid, pid = struct.unpack_from("<2H", blk, 82)
check("pad identifies as Xbox 360 (045e:028e)", (vid, pid) == (0x045E, 0x028E))
absmax = struct.unpack_from("<64i", blk, 92)
check("axis ranges set", absmax[U.ABS_X] == 32767 and absmax[U.ABS_Z] == 255 and absmax[U.ABS_HAT0X] == 1)
check("uinput problem is explained in words here (no /dev/uinput in sandbox)", "uinput" in U.uinput_problem())

# ---------------------------------------------------------------- encoder and ICE helpers
check("hardware encoder preferred over x264", S.pick_encoder({"x264enc", "nvh264enc"})[0] == "nvh264enc")
check("falls back to x264", S.pick_encoder({"x264enc"})[0] == "x264enc")
check("honours a specific request", S.pick_encoder({"x264enc", "nvh264enc"}, "x264enc")[0] == "x264enc")
check("none available -> None", S.pick_encoder({"videoconvert"})[0] is None)
check("bitrate flows into props", S.pick_encoder({"x264enc"}, kbps=5000)[1]["bitrate"] == 5000)
stun, turns = S.ice_to_gst([{"urls": ["stun:stun.l.google.com:19302"]},
                            {"urls": "turn:relay.example.com:3478?transport=udp", "username": "u s", "credential": "p@ss"}])
check("STUN converted", stun == "stun://stun.l.google.com:19302", str(stun))
check("TURN converted with escaped credentials", turns == ["turn://u%20s:p%40ss@relay.example.com:3478"], str(turns))
check("empty ICE ok", S.ice_to_gst([]) == (None, []) and S.ice_to_gst(None) == (None, []))

# ---------------------------------------------------------------- doctor text
t = S.doctor_lines(None, "x11", "", True)
check("doctor: no GStreamer -> install commands", any("apt install" in l for l in t) and t[0].startswith("[X]"))
full = {"webrtcbin", "nicesrc", "rtph264pay", "h264parse", "videoconvert", "opusenc", "rtpopuspay", "pulsesrc", "ximagesrc", "x264enc"}
t = S.doctor_lines(full, "x11", "", True)
check("doctor: all good", not any(l.startswith("[X") for l in t), "\n".join(t))
check("doctor: says software encoder is slower", any("software" in l for l in t))
t = S.doctor_lines(full - {"webrtcbin"}, "wayland", "No permission for /dev/uinput", True)
check("doctor: flags missing webrtcbin, pipewire and uinput", sum(l.startswith("[X") for l in t) == 3, "\n".join(t))
t = S.doctor_lines(full | {"nvh264enc"}, "x11", "", True)
check("doctor: hardware encoder noted", any("nvh264enc" in l and "hardware" in l for l in t))

# ---------------------------------------------------------------- host timing
T = S.HostTimer()
for i in range(100):
    pts = i * 16_666_667
    t0 = 1000.0 + i * 0.0166
    T.mark("cap", pts, t0, age_ms=2.0); T.mark("enc_in", pts, t0 + 0.001)
    T.mark("enc_out", pts, t0 + 0.001 + (0.005 if i % 10 == 9 else 0.002)); T.mark("pay", pts, t0 + 0.0075 + (0.003 if i % 10 == 9 else 0.0))
sm = T.summary()
check("timer: every frame counted", sm["frames"] == 100, str(sm))
check("timer: convert ~1 ms", abs(sm["convertMs"] - 1.0) < 0.01, str(sm["convertMs"]))
check("timer: encode mean is 2.3 ms (90x2 + 10x5)", abs(sm["encodeMs"] - 2.3) < 0.01, str(sm["encodeMs"]))
check("timer: p95 sees the slow frames", abs(sm["encodeP95"] - 5.0) < 0.01, str(sm["encodeP95"]))
check("timer: capture age reported", abs(sm["captureAgeMs"] - 2.0) < 0.01)
check("timer: total spans cap->pay", abs(sm["totalMs"] - 7.8) < 0.1, str(sm["totalMs"]))
T2 = S.HostTimer(max_pending=10)
for i in range(50): T2.mark("cap", i, 1.0)
check("timer: frames that never finish don't pile up", len(T2.pending) <= 10 and len(T2.order) <= 10)
T2.mark("enc_in", 999, 1.0); T2.mark("pay", 999, 1.0)
check("timer: unknown frames ignored", T2.summary() == {} or T2.frames == 0)
check("timer: empty summary is empty", S.HostTimer().summary() == {})
T3 = S.HostTimer(window=5)
for i in range(20):
    for st, dt in (("cap", 0), ("enc_in", 0.001), ("enc_out", 0.003), ("pay", 0.004)): T3.mark(st, i, 10 + i + dt)
check("timer: keeps only the recent window", len(T3.samples["encodeMs"]) == 5)

# ---------------------------------------------------------------- session orchestration, real server
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

class FakePad:
    def __init__(self): self.state, self.applied, self.released = U.neutral(), [], 0
    def apply(self, st): self.state = st; self.applied.append(st)
    def release_all(self): self.state = U.neutral(); self.released += 1

class FakeBackend:
    instances = []
    def __init__(self, session): self.s, self.log = session, []; FakeBackend.instances.append(self)
    def start(self): self.log.append("start"); self.s.on_offer("v=0 OFFER"); self.s.on_ice(0, "candidate:1 1 udp 1 1.2.3.4 5 typ host")
    def stop(self): self.log.append("stop")
    def set_answer(self, sdp): self.log.append(("answer", sdp))
    def add_ice(self, i, c): self.log.append(("ice", i, c))
    def send_ctl(self, o): self.log.append(("ctl", o))

def api(base, key, path="/sig?wait=0", body=None):
    req = urllib.request.Request(f"{base}{path}{'&' if '?' in path else '?'}t={key}", data=None if body is None else json.dumps(body).encode(),
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def poll_until(base, key, want, timeout=5, also=()):
    got, end = [], time.time() + timeout
    while time.time() < end and not all(any(m["type"] == w for m in got) for w in (want, *also)):
        got += api(base, key, "/sig?wait=1")["msgs"]
    return got

port, hport = free_port(), free_port()
srv = subprocess.Popen([sys.executable, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server.py"), str(port), str(hport)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(1.0)
base = f"http://127.0.0.1:{hport}"
def match():
    def s(client, ch):
        c = socket.create_connection(("127.0.0.1", port))
        c.sendall((json.dumps({"v": 1, "op": "search", "game": "ps2:urban-reign", "lanes": ["stream"], "client": client,
                               "can_host": {"stream": ch}, "info": {"stream": {"backend": "own"}}}) + "\n").encode())
        return c
    a = s("h", True); time.sleep(0.2); b = s("g", False)
    def rd(c):
        buf = b""
        while not buf.endswith(b"\n"): buf += c.recv(4096)
        return json.loads(buf)
    return rd(a)["session"]["key"], rd(b)["session"]["key"], (a, b)
try:
    hk, gk, socks = match()
    csv = tempfile.mktemp(suffix=".csv")
    pad, clock = FakePad(), [100.0]
    done = threading.Event()
    sig = S.Signaling(base, hk, on_msg=lambda m: sess.on_signal(m), on_end=lambda why: (sess.end(why), done.set()))
    sess = S.Session(sig, lambda s: FakeBackend(s), pad, slot=2, stats_path=csv, clock=lambda: clock[0])
    sig.start()
    api(base, gk, body={"type": "hello"})
    got = poll_until(base, gk, "offer", also=("ice",))
    types = [m["type"] for m in got]
    check("hello -> host starts backend and sends offer + ice", "offer" in types and "ice" in types, str(types))
    check("offer text relayed intact", [m for m in got if m["type"] == "offer"][0]["sdp"] == "v=0 OFFER")
    be = FakeBackend.instances[-1]
    api(base, gk, body={"type": "answer", "sdp": "v=0 ANSWER"})
    api(base, gk, body={"type": "ice", "candidate": "candidate:9 1 udp 1 9.9.9.9 9 typ host", "sdpMLineIndex": 0})
    time.sleep(1.0)
    check("answer reaches backend", ("answer", "v=0 ANSWER") in be.log, str(be.log))
    check("ice reaches backend", any(isinstance(x, tuple) and x[0] == "ice" and "9.9.9.9" in x[2] for x in be.log))
    api(base, gk, body={"type": "ice", "candidate": 5})
    api(base, gk, body={"type": "answer"})
    time.sleep(0.5)
    check("malformed signaling ignored without crashing", not sess.ended.is_set())

    # control channel
    sess.on_ctl({"t": "ping", "ts": 12.5}); sess.on_ctl({"t": "hi"})
    check("ping answered with pong", ("ctl", {"t": "pong", "ts": 12.5}) in be.log)
    check("guest told its player slot", ("ctl", {"t": "slot", "n": 2}) in be.log)

    # input: ordering, duplicates, wrap-around, stuck buttons
    sess.on_pad(pkt(10, 1)); sess.on_pad(pkt(10, 1)); sess.on_pad(pkt(9, 0)); sess.on_pad(pkt(11, 3))
    check("duplicate and stale packets dropped", [a["seq"] for a in pad.applied] == [10, 11], str([a["seq"] for a in pad.applied]))
    for sq in range(12, 256): sess.on_pad(pkt(sq, 3))
    sess.on_pad(pkt(0, 3)); sess.on_pad(pkt(1, 0))
    check("sequence wraps 255 -> 0 correctly", [a["seq"] for a in pad.applied][-3:] == [255, 0, 1])
    clock[0] += 5; sess.on_pad(pkt(200, 3))
    check("after a long silence a jumped-ahead number is accepted again", pad.applied[-1]["seq"] == 200)
    sess.on_pad(pkt(1, 0))
    sess.on_pad(b"garbage")
    check("garbage ignored", pad.applied[-1]["seq"] == 1)
    sess.on_pad(pkt(2, 1)); clock[0] += 0.2
    check("watchdog leaves a held button alone while heartbeats are fresh", not sess.watchdog_tick() and pad.state["buttons"] == 1)
    clock[0] += 0.6
    check("watchdog releases a button if the guest goes silent", sess.watchdog_tick() and pad.state["buttons"] == 0)
    check("watchdog quiet when nothing is held", not sess.watchdog_tick())
    r = pad.released; sess.on_state("disconnected")
    check("disconnect releases the pad", pad.released == r + 1)

    # stats log
    sess.on_ctl({"t": "stats", "s": {"rttMs": 4.2, "jitterBufMs": 8, "decodeMs": 1.5, "addedMs": 11.6, "fps": 60, "kbps": 8000, "lost": 0, "dropped": 0, "w": 1280, "h": 720}})
    sess.on_ctl({"t": "stats", "s": {"rttMs": 99}})
    rows = open(csv).read().strip().split("\n")
    check("stats written to CSV once per 2s", len(rows) == 2 and rows[0].startswith("time,rttMs") and ",4.2," in rows[1], str(rows))
    check("CSV has the host-timing columns (empty before any host stats)", rows[0].endswith("captureAgeMs,convertMs,encodeMs,encodeP95,payMs,totalMs") and rows[1].endswith(",,,,,"), rows[0][-80:])
    sess.host_stats = {"encodeMs": 2.3, "totalMs": 7.8}; clock[0] += 3
    sess.on_ctl({"t": "stats", "s": {"rttMs": 5}})
    rows = open(csv).read().strip().split("\n")
    check("CSV row includes host timing once known", len(rows) == 3 and ",2.3,,,7.8" in rows[2], rows[2])

    # re-hello (guest refreshed the page) restarts cleanly
    n_before = len(FakeBackend.instances); api(base, gk, body={"type": "hello"}); time.sleep(1.2)
    check("second hello restarts the backend", len(FakeBackend.instances) == n_before + 1 and "stop" in be.log)

    # ending
    sess.on_state("failed")
    got = poll_until(base, gk, "bye", 4)
    check("host tells guest 'bye' when it ends", any(m["type"] == "bye" for m in got))
    check("session ended flag set and pad released", sess.ended.is_set() and pad.state["buttons"] == 0)
    sig.stop()

    # guest leaving ends the host session
    hk2, gk2, socks2 = match()
    pad2 = FakePad(); done2 = threading.Event()
    sig2 = S.Signaling(base, hk2, on_msg=lambda m: sess2.on_signal(m), on_end=lambda why: sess2.end(why))
    sess2 = S.Session(sig2, lambda s: FakeBackend(s), pad2, stats_path=csv); sig2.start()
    api(base, gk2, body={"type": "bye"}); time.sleep(1.5)
    check("guest 'bye' ends host session", sess2.ended.is_set())
    sig2.stop()

    # the host's long-poll notices a dropped session (404)
    hk3, gk3, _ = match()
    ended3 = threading.Event(); sig3 = S.Signaling(base, "not-a-key", on_msg=lambda m: None, on_end=lambda why: ended3.set()); sig3.start()
    check("bad key -> signaling reports the session is over", ended3.wait(4))
    sig3.stop()
finally:
    srv.terminate()
print("ALL PASSED" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
