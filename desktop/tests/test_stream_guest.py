"""The native player's plain logic (no GStreamer, no window): controller packets are exactly what the host reads,
SDL controller and keyboard mapping, heartbeats, the numbers, the stats file, and the words on the screen."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import stream_guest as G, uinput_pad

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""), flush=True)

# ---- packets
st = {"buttons": (1 << 0) | (1 << 14), "ax": [0.5, -1.0, 0.0, 1.0], "lt": 0, "rt": 255}
p = G.pack(st, 7)
r = uinput_pad.parse_packet(p)
check("packet is 16 bytes, version 1", len(p) == 16 and p[0] == 1)
check("the host reads back exactly what was packed", r == {"seq": 7, "buttons": 16385, "ax": (16384, -32767, 0, 32767), "lt": 0, "rt": 255}, r)
check("sequence wraps at 256", uinput_pad.parse_packet(G.pack(st, 256))["seq"] == 0)
check("sticks are clamped", uinput_pad.parse_packet(G.pack({**st, "ax": [3, -3, 0, 0]}, 1))["ax"][:2] == (32767, -32767))

# ---- SDL game controller -> W3C standard gamepad
allb = {b: 1 for b in range(15)}
c = G.controller_state(allb, [0, 0, 0, 0, 32767, 0])
check("every SDL button maps to its standard bit (A..d-pad, Back, Start, Guide, sticks)",
      c["buttons"] == sum(1 << n for n in (0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16)), bin(c["buttons"]))
check("A is bit 0, Back bit 8, Start bit 9, Guide bit 16",
      G.controller_state({0: 1}, [0] * 6)["buttons"] == 1 and G.controller_state({4: 1}, [0] * 6)["buttons"] == 1 << 8
      and G.controller_state({6: 1}, [0] * 6)["buttons"] == 1 << 9 and G.controller_state({5: 1}, [0] * 6)["buttons"] == 1 << 16)
check("shoulders are 4 and 5, d-pad 12-15", G.controller_state({9: 1, 10: 1}, [0] * 6)["buttons"] == 0b110000
      and G.controller_state({11: 1, 12: 1, 13: 1, 14: 1}, [0] * 6)["buttons"] == 0b1111 << 12)
c = G.controller_state({}, [-32768, 32767, 1000, -2000, 32767, 16384])
check("sticks: full left/down, small moves are dead zone", c["ax"][0] == -1.0 and c["ax"][1] == 1.0 and c["ax"][2:] == [0.0, 0.0], c["ax"])
check("triggers become 0-255", c["lt"] == 255 and 126 <= c["rt"] <= 128, c)

# ---- keyboard, and the two together
check("keyboard keys match the web player (Z=A, X=B, arrows=d-pad, Enter=Start, Shift=Back)",
      G.keyboard_buttons({"z", "x", "up", "right", "return", "left shift"}) == (1 | 2 | 1 << 12 | 1 << 15 | 1 << 9 | 1 << 8))
check("unknown keys do nothing", G.keyboard_buttons({"f5", "escape", "1"}) == 0)
m = G.combine(G.controller_state({0: 1}, [0] * 6), G.keyboard_buttons({"x"}))
check("controller and keyboard add up", m["buttons"] == 3)
check("no controller: keyboard alone", G.combine(None, 1 << 9)["buttons"] == 1 << 9)
check("trigger bits count as full triggers", G.combine(None, (1 << 6) | (1 << 7))["lt"] == 255)

# ---- send on change, heartbeat every 50 ms
t = [0.0]
s = G.InputSender(clock=lambda: t[0])
idle = G.combine(None, 0)
a = s.next(idle); t[0] = 0.01
b = s.next(idle); t[0] = 0.02
c2 = s.next(G.combine(None, 1)); t[0] = 0.08
d = s.next(G.combine(None, 1))
check("first state is sent", a is not None)
check("unchanged within 50 ms: nothing", b is None)
check("a change is sent at once", c2 is not None and uinput_pad.parse_packet(c2)["buttons"] == 1)
check("unchanged after 50 ms: heartbeat", d is not None)
check("sequence numbers go up by one", [uinput_pad.parse_packet(x)["seq"] for x in (a, c2, d)] == [1, 2, 3])

# ---- numbers
S = G.Stats(15)
S.frames = 0; S.update(10.0, 0, 0)
S.frames = 60; S.add_decode(2.0); S.add_decode(4.0); S.rtt = 10.0
x = S.update(11.0, 125000, 3)
check("fps, kbps, decode, lost from one second", x["fps"] == 60 and x["kbps"] == 1000 and x["decodeMs"] == 3.0 and x["lost"] == 3, x)
check("added delay = half the round trip + buffer + decode", x["addedMs"] == 23.0, x)
lines = G.overlay_lines(x, {"encodeMs": 2.5}, 99)
check("the F2 overlay shows round trip, fps, decode time and lost packets",
      any("round trip 10.0" in l for l in lines) and any("60 fps" in l for l in lines)
      and any("decode 3.0" in l for l in lines) and any("lost packets 3" in l for l in lines), lines)

f = os.path.join(tempfile.mkdtemp(), "stream-stats.csv")
G.write_stats_row(f, x, {"encodeMs": 2.5}); G.write_stats_row(f, x, {})
rows = open(f).read().splitlines()
check("stats file: same columns as the host's file", rows[0] == "time,rttMs,jitterBufMs,decodeMs,addedMs,fps,kbps,lost,dropped,w,h,"
      "captureAgeMs,convertMs,encodeMs,encodeP95,payMs,totalMs" and len(rows) == 3, rows[:2])

# ---- words
check("goodbye with the host's real reason", G.bye_text("capturing or encoding the game failed on the computer: no encoder.")
      == "The stream stopped on your friend's computer: capturing or encoding the game failed on the computer: no encoder.")
check("goodbye without a reason", G.bye_text("") == "Your friend ended the session.")
check("connection failed, no relay: says why and what helps", "no relay server" in G.end_text("ice failed", relay=False))
check("connection failed with a relay: says even the relay didn't work", "relay server" in G.end_text("ice failed", relay=True)
      and "no relay" not in G.end_text("ice failed", relay=True))
check("other reasons pass through", G.end_text("something else") == "something else")

# ---- what this computer can do
check("hardware decoders come first", G.pick_decoders({"avdec_h264", "vah264dec", "nvh264dec"}) == ["nvh264dec", "vah264dec", "avdec_h264"])
check("a chosen decoder is used alone", G.pick_decoders({"avdec_h264", "vah264dec"}, "avdec_h264") == ["avdec_h264"])
full = {n for n, _ in G.NEEDED} | {"avdec_h264"}
check("check: all there", not any(l.startswith("[X") for l in G.check_lines(full)))
check("check: no decoder is named with what to install", any("no H.264 video decoder" in l for l in G.check_lines(full - {"avdec_h264"})))
check("check: missing webrtcbin names its package", any("webrtcbin" in l and "plugins-bad" in l for l in G.check_lines(full - {"webrtcbin"})))
check("check: no GStreamer at all says how to install it", G.check_lines(None)[0].startswith("[X") and "apt install" in G.check_lines(None)[1])

print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
