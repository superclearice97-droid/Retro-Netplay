"""Guest side of the built-in streamer: the player window that shows a friend's game.

The desktop app opens this by itself after a match (Friends > Join > your friend taps Allow). It never uses a web
browser: it talks to the matchmaker over the stream connection (stream_signal.py), receives the host's WebRTC
stream with GStreamer (the same stack as stream_host.py), shows it in an SDL window, and sends the controller back.

  python3 stream_guest.py --mm HOST:PORT --key KEY [--name Bea]     (the app starts this for you)
  python3 stream_guest.py --check                                    (what this computer can do, no network)

Keys in the window: F11 or Alt+Enter = full screen, F2 = numbers (round trip, fps, decode time, lost packets),
Esc twice = leave. A controller (SDL game controller) is Player 2 on the host; the keyboard works too (see KEYMAP).
All packing, mapping and wording below is plain Python and unit-tested; Media (GStreamer) and Window (SDL) are run
by the automatic end-to-end test with a pretend screen.
"""
import argparse
import ctypes
import json
import os
import platform
import queue
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATS_CSV = os.path.expanduser("~/.netplay-matchmaker/stream-stats.csv")
FONT = os.path.join(HERE, "fonts", "DejaVuSansMono.ttf")


def log(*a):
    print("[player]", *a, flush=True)


# ===================================================================== controller packet (PROTOCOL.md)
PACKET = struct.Struct("<BBI4h2B")      # ver, seq, buttons, lx ly rx ry, lt rt  (16 bytes, little endian)
HEARTBEAT = 0.05                        # unchanged input is re-sent every 50 ms (the host lets go after 0.5 s)
DEAD = 0.1                              # stick dead zone, same as the web player


def pack(state, seq):
    """state = {"buttons", "ax" (4 floats -1..1), "lt", "rt" (0..255)} -> the 16-byte v1 packet."""
    ax = [max(-32767, min(32767, int(round(a * 32767)))) for a in state["ax"]]
    return PACKET.pack(1, seq & 255, state["buttons"] & 0xFFFFFFFF, *ax, int(state["lt"]) & 255, int(state["rt"]) & 255)


# W3C "standard gamepad" button numbers: 0 A, 1 B, 2 X, 3 Y, 4 LB, 5 RB, 6 LT, 7 RT, 8 Back, 9 Start,
# 10 L3, 11 R3, 12-15 d-pad up/down/left/right, 16 Guide.
# SDL game controller button -> W3C button (SDL order: a b x y back guide start leftstick rightstick
# leftshoulder rightshoulder dpup dpdown dpleft dpright)
SDL_BUTTONS = {0: 0, 1: 1, 2: 2, 3: 3, 4: 8, 5: 16, 6: 9, 7: 10, 8: 11, 9: 4, 10: 5, 11: 12, 12: 13, 13: 14, 14: 15}
# keyboard fallback (SDL key names, lower case): the same keys as the web player
KEYMAP = {"z": 0, "x": 1, "a": 2, "s": 3, "q": 4, "w": 5, "left shift": 8, "right shift": 8, "return": 9,
          "up": 12, "down": 13, "left": 14, "right": 15}


def dead(v):
    return 0.0 if abs(v) < DEAD else v


def controller_state(buttons, axes):
    """SDL game controller readings -> packet state. buttons = {sdl button: pressed}, axes = 6 raw SDL values
    (leftx lefty rightx righty: -32768..32767, up = negative; triggers: 0..32767)."""
    b = 0
    for sdl, w3c in SDL_BUTTONS.items():
        if buttons.get(sdl):
            b |= 1 << w3c
    ax = [dead(max(-1.0, a / 32767.0)) for a in axes[:4]]
    lt, rt = (min(255, max(0, round(t * 255 / 32767))) for t in axes[4:6])
    return {"buttons": b, "ax": ax, "lt": lt, "rt": rt}


def keyboard_buttons(held):
    b = 0
    for k in held:
        if k in KEYMAP:
            b |= 1 << KEYMAP[k]
    return b


def combine(pad, key_bits):
    """Controller (or None) + keyboard -> one state. Trigger buttons count as fully pressed triggers."""
    st = {"buttons": 0, "ax": [0.0] * 4, "lt": 0, "rt": 0} if pad is None else {**pad, "ax": list(pad["ax"])}
    st["buttons"] |= key_bits
    if st["buttons"] & (1 << 6):
        st["lt"] = 255
    if st["buttons"] & (1 << 7):
        st["rt"] = 255
    return st


class InputSender:
    """Send a packet when the input changes, and a heartbeat every 50 ms while it doesn't."""

    def __init__(self, clock=time.monotonic):
        self.seq, self.last, self.at, self.sent, self.clock = 0, None, -1.0, 0, clock

    def next(self, state, force=False):
        """-> packet bytes to send now, or None."""
        sig = (state["buttons"], tuple(round(a, 4) for a in state["ax"]), state["lt"], state["rt"])
        now = self.clock()
        if not force and sig == self.last and now - self.at < HEARTBEAT:
            return None
        self.last, self.at = sig, now
        self.seq = (self.seq + 1) & 255
        self.sent += 1
        return pack(state, self.seq)


# ===================================================================== numbers (F2) and the stats file
class Stats:
    """What the player sees, in the same names the web player used (so the host's log and file stay the same)."""

    def __init__(self, jitter_ms):
        self.jitter_ms = jitter_ms
        self.frames = 0          # frames that reached the screen
        self.dropped = 0         # decoded frames replaced by a newer one before they could be shown
        self.decode = []         # recent decode times (ms)
        self.rtt = None
        self.w = self.h = 0
        self.prev = None
        self.s = {}

    def add_decode(self, ms):
        self.decode.append(ms)
        del self.decode[:-120]

    def update(self, now, bytes_rx, lost):
        """Call about once a second. Returns the stats dict sent to the host."""
        s = self.s
        if self.prev:
            t0, b0, f0 = self.prev
            secs = max(now - t0, 1e-3)
            s["kbps"] = round((bytes_rx - b0) * 8 / secs / 1000)
            s["fps"] = round((self.frames - f0) / secs)
        self.prev = (now, bytes_rx, self.frames)
        s["decodeMs"] = round(sum(self.decode) / len(self.decode), 1) if self.decode else None
        s["jitterBufMs"] = self.jitter_ms
        s["rttMs"] = round(self.rtt, 1) if self.rtt is not None else None
        s["lost"], s["dropped"], s["w"], s["h"] = lost, self.dropped, self.w, self.h
        s["addedMs"] = (round(s["rttMs"] / 2 + s["jitterBufMs"] + s["decodeMs"], 1)
                        if None not in (s["rttMs"], s["decodeMs"]) else None)
        return dict(s)


CSV_KEYS = ["rttMs", "jitterBufMs", "decodeMs", "addedMs", "fps", "kbps", "lost", "dropped", "w", "h"]
CSV_HOST_KEYS = ["captureAgeMs", "convertMs", "encodeMs", "encodeP95", "payMs", "totalMs"]   # same columns as the host's


def write_stats_row(path, s, host):
    row = [time.strftime("%H:%M:%S")] + [str(s.get(k, "")) for k in CSV_KEYS] + [str(host.get(k, "")) for k in CSV_HOST_KEYS]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a") as f:
            if new:
                f.write(",".join(["time"] + CSV_KEYS + CSV_HOST_KEYS) + "\n")
            f.write(",".join("" if v == "None" else v for v in row) + "\n")
    except OSError:
        pass


def overlay_lines(s, host, packets):
    n = lambda v: "?" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v))
    lines = [f"{s.get('w', 0)}x{s.get('h', 0)} @ {n(s.get('fps'))} fps   {n(s.get('kbps'))} kbps",
             f"round trip {n(s.get('rttMs'))} ms",
             f"jitter buffer {n(s.get('jitterBufMs'))} ms   decode {n(s.get('decodeMs'))} ms",
             f"lost packets {n(s.get('lost'))}   dropped frames {n(s.get('dropped'))}   inputs sent {packets}",
             f"added by network + playback ~ {n(s.get('addedMs'))} ms"]
    if host:
        lines.append("host: capture age %s  convert %s  encode %s (p95 %s)  pack %s ms" % tuple(
            n(host.get(k)) for k in ("captureAgeMs", "convertMs", "encodeMs", "encodeP95", "payMs")))
    else:
        lines.append("host timing: not available")
    return lines


# ===================================================================== words on the screen
def bye_text(why):
    why = str(why or "").strip().rstrip(".")[:300]
    if not why:
        return "Your friend ended the session."
    return f"The stream stopped on your friend's computer: {why}."


def end_text(why, relay=False):
    """Why the player stopped, for the screen. why = a stream_signal ending, or one of the player's own."""
    w = str(why)
    if w in ("session closed", "session ended"):
        return "The stream has ended."
    if w == "lost contact with the matchmaker":
        return ("Lost contact with the matchmaker, so the stream ended. Check your internet connection and press Join "
                "again.")
    if w == "ice failed":
        if relay:
            return ("Couldn't connect to your friend's computer, not even through the relay server. "
                    "One of your networks may block it (a work or school network often does).")
        return ("Couldn't connect to your friend's computer. One of your routers blocks direct connections, and this "
                "matchmaker has no relay server for that. Ask whoever runs the matchmaker to add one (README: "
                "\"TURN relay\"), or try from the same Wi-Fi.")
    if w == "no host":
        return ("Your friend's computer didn't answer. Retro-Netplay must be open there with the game running. "
                "Press Join again.")
    if w == "no video":
        if relay:
            return ("The video hasn't arrived, not even through the relay server. One of your networks may block it "
                    "(a work or school network often does).")
        return ("The video hasn't arrived: your two devices can't reach each other directly, and this matchmaker has no "
                "relay server for strict routers. Try the same Wi-Fi (not a guest network), or ask whoever runs the "
                "matchmaker to add one (README: \"TURN relay\").")
    return w


DECODERS = ["nvh264dec", "vah264dec", "d3d11h264dec", "d3d12h264dec", "vtdec_hw", "vtdec", "vaapih264dec",
            "avdec_h264", "openh264dec"]
HARDWARE = {"nvh264dec", "vah264dec", "d3d11h264dec", "d3d12h264dec", "vtdec_hw", "vtdec", "vaapih264dec"}
NEEDED = [("webrtcbin", "gstreamer1.0-plugins-bad"), ("nicesrc", "gstreamer1.0-nice"),
          ("rtph264depay", "gstreamer1.0-plugins-good"), ("h264parse", "gstreamer1.0-plugins-bad"),
          ("opusdec", "gstreamer1.0-plugins-base"), ("rtpopusdepay", "gstreamer1.0-plugins-good"),
          ("videoconvert", "gstreamer1.0-plugins-base"), ("appsink", "gstreamer1.0-plugins-base")]


def pick_decoders(available, prefer="auto"):
    """H.264 decoders to try, hardware first. The player falls back to the next one if one fails."""
    if prefer != "auto":
        return [prefer] if prefer in available else []
    return [d for d in DECODERS if d in available]


def check_lines(elements, sdl_problem=""):
    """Pure: what this computer can do as the joining player. elements = set of GStreamer element names or None."""
    if elements is None and sys.platform in ("win32", "darwin"):
        return ["[X ] The player window needs GStreamer, which this copy of Retro-Netplay doesn't carry yet on "
                + ("Windows" if sys.platform == "win32" else "macOS") + ". Joining a friend's stream works from the "
                "Linux download for now; the Windows and Mac downloads with the player built in come next."]
    if elements is None:
        return ["[X ] GStreamer for Python is not installed (needed to show the stream).",
                "    Debian/Ubuntu: sudo apt install python3-gi gir1.2-gst-plugins-bad-1.0 gstreamer1.0-plugins-good "
                "gstreamer1.0-plugins-bad gstreamer1.0-nice gstreamer1.0-libav",
                "    Fedora: sudo dnf install python3-gobject gstreamer1-plugins-good gstreamer1-plugins-bad-free "
                "libnice-gstreamer1 gstreamer1-plugin-libav",
                "    Arch / CachyOS / Manjaro: sudo pacman -S --needed python-gobject gst-plugins-good gst-plugins-bad "
                "libnice gst-libav"]
    out = []
    for el, pkg in NEEDED:
        out.append(f"[{'OK' if el in elements else 'X '}] {el}" + ("" if el in elements else f"   (package: {pkg})"))
    decs = pick_decoders(elements)
    if not decs:
        out.append("[X ] no H.264 video decoder. Install gstreamer1.0-libav (gst-libav), or your graphics card's "
                   "decoder (gstreamer1.0-plugins-bad with VA-API or NVIDIA drivers).")
    else:
        hw = [d for d in decs if d in HARDWARE]
        out.append(f"[OK] video decoder: {decs[0]}" + ("  (graphics card)" if decs[0] in HARDWARE else "  (software)")
                   + (f", then {', '.join(decs[1:])}" if len(decs) > 1 else ""))
        if not hw:
            out.append("     Decoding on the graphics card is a little faster: install gstreamer1.0-vaapi / "
                       "gst-plugin-va (Intel/AMD) or the NVIDIA driver.")
    out.append("[OK] window and controllers (SDL2)" if not sdl_problem else "[X ] window and controllers: " + sdl_problem)
    return out


# ===================================================================== GStreamer: receive the stream
class Media:
    """webrtcbin -> (H.264 decoder -> appsink) and (Opus -> speakers), plus the 'pad' and 'ctl' data channels.
    Everything it notices is put on `events` for the window's thread: ("answer", sdp), ("ice", index, candidate),
    ("state", connection state), ("open", label), ("ctl", dict), ("frame",), ("error", text)."""

    def __init__(self, ice, events, jitter_ms=15, decoder="auto", audio=True, headless=False, relay_only=False):
        self.ice, self.events, self.jitter_ms, self.audio, self.headless = ice, events, jitter_ms, audio, headless
        self.relay_only = relay_only
        self.decoders = pick_decoders(available_elements() or set(), decoder)
        self.pipe = self.wb = self.pad_ch = self.ctl_ch = None
        self.video, self.video_src = [], None
        self.lock = threading.Lock()
        self.sample = None           # newest decoded frame, for the window
        self.stats = None            # set by the window: Stats
        self.dec_in = {}

    def start(self):
        from gi.repository import Gst
        import stream_host
        if not self.decoders:
            raise RuntimeError("This computer has no H.264 video decoder for GStreamer. Install gstreamer1.0-libav "
                               "(gst-libav) and try again.")
        self.pipe = Gst.Pipeline.new("player")
        self.wb = self._mk("webrtcbin", {"bundle-policy": "max-bundle", "latency": self.jitter_ms})
        stun, turns = stream_host.ice_to_gst(self.ice)
        if stun:
            self.wb.set_property("stun-server", stun)
        for t in turns:
            self.wb.emit("add-turn-server", t)
        if self.relay_only:
            stream_host.GstBackend._set(self.wb, "ice-transport-policy", "relay")
        self.pipe.add(self.wb)
        self.wb.connect("on-ice-candidate", lambda el, idx, cand: self.events.put(("ice", idx, cand)))
        self.wb.connect("notify::connection-state",
                        lambda el, _p: self.events.put(("state", el.get_property("connection-state").value_nick)))
        self.wb.connect("notify::ice-connection-state",
                        lambda el, _p: self.events.put(("ice_state", el.get_property("ice-connection-state").value_nick)))
        self.wb.connect("pad-added", self._on_pad)
        self.wb.connect("on-data-channel", self._on_channel)
        bus = self.pipe.get_bus()
        bus.add_signal_watch()      # (watched by poll_bus from the window's loop; no GLib loop needed)
        self.bus = bus
        self.pipe.set_state(Gst.State.PLAYING)
        log("decoders to try:", ", ".join(self.decoders))

    def _mk(self, factory, props=None):
        from gi.repository import Gst
        import stream_host
        el = Gst.ElementFactory.make(factory, None)
        if el is None:
            raise RuntimeError(f"GStreamer element '{factory}' is missing. Run the player with --check for what to install.")
        for k, v in (props or {}).items():
            stream_host.GstBackend._set(el, k, v)
        return el

    # ---- negotiation (the host makes the offer; we answer)
    def set_offer(self, sdp):
        from gi.repository import Gst, GstSdp, GstWebRTC
        ok, msg = GstSdp.SDPMessage.new()
        GstSdp.sdp_message_parse_buffer(bytes(sdp.encode()), msg)
        offer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.OFFER, msg)

        def answered(promise, _el, _u):
            reply = promise.get_reply()
            answer = reply.get_value("answer") if reply else None
            if answer is None:
                return self.events.put(("error", "the video connection couldn't be set up (WebRTC answer)"))
            self.wb.emit("set-local-description", answer, Gst.Promise.new())
            self.events.put(("answer", answer.sdp.as_text()))

        def remote_set(promise, _el, _u):
            self.wb.emit("create-answer", None, Gst.Promise.new_with_change_func(answered, None, None))
        self.wb.emit("set-remote-description", offer, Gst.Promise.new_with_change_func(remote_set, None, None))

    def add_ice(self, index, candidate):
        self.wb.emit("add-ice-candidate", int(index), candidate)

    # ---- media branches
    def _on_pad(self, el, pad):
        caps = pad.get_current_caps()
        media = caps.get_structure(0).get_string("media") if caps else ""
        if media == "video":
            self.video_src = pad
            self._build_video()
        elif media == "audio":
            sink = (self._mk("fakesink") if self.headless or not self.audio else self._mk("autoaudiosink"))
            self._set_sync(sink)
            a = [self._mk("rtpopusdepay"), self._mk("opusdec"), self._mk("audioconvert"), self._mk("audioresample"), sink]
            self._add_chain(pad, a)
        else:
            self._add_chain(pad, [self._mk("fakesink")])

    def _set_sync(self, sink):
        """No waiting for the clock: show and play everything the moment it is decoded."""
        if sink.find_property("sync"):
            sink.set_property("sync", False)
        if hasattr(sink, "connect") and sink.get_factory().get_name() == "autoaudiosink":
            def child(_bin, el, _name):
                for k, v in (("sync", False), ("buffer-time", 40000), ("latency-time", 10000)):
                    if el.find_property(k):
                        el.set_property(k, v)
            try:
                sink.connect("deep-element-added", lambda b, sub, el: child(b, el, ""))
            except TypeError:
                pass

    def _add_chain(self, src_pad, els):
        from gi.repository import Gst
        for e in els:
            self.pipe.add(e)
        for a, b in zip(els, els[1:]):
            if not a.link(b):
                self.events.put(("error", f"couldn't connect {a.get_name()} to {b.get_name()}"))
        for e in els:
            e.sync_state_with_parent()
        if src_pad.link(els[0].get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            self.events.put(("error", "couldn't connect the incoming stream"))
        return els

    def _build_video(self):
        from gi.repository import Gst, GstApp  # noqa: F401  (GstApp makes appsink's signals available)
        dec_name = self.decoders[0]
        depay = self._mk("rtph264depay", {"request-keyframe": True, "wait-for-keyframe": True})
        dec = self._mk(dec_name)
        sink = self._mk("appsink", {"sync": False, "max-buffers": 1, "drop": True, "emit-signals": True,
                                    "caps": Gst.Caps.from_string("video/x-raw,format={NV12,I420}")})
        sink.connect("new-sample", self._on_sample)
        conv = self._mk("videoconvert")    # does nothing when the decoder already gives NV12 / I420
        self.video = [depay, self._mk("h264parse"), dec, conv, sink]
        self.decoder = dec
        self._probe_decode(dec)
        self._add_chain(self.video_src, self.video)
        log("video decoder:", dec_name)
        self.events.put(("decoder", dec_name))

    def next_decoder(self):
        """The decoder failed (a graphics card can list one it can't run): rebuild the video part with the next one."""
        from gi.repository import Gst
        if len(self.decoders) < 2 or self.video_src is None:
            return False
        failed = self.decoders.pop(0)
        log(f"decoder {failed} failed; trying {self.decoders[0]}")

        def swap(pad, _info):      # while the incoming video waits at this pad, so nothing flows into a gap
            peer = pad.get_peer()
            if peer:
                pad.unlink(peer)
            for e in self.video:
                e.set_state(Gst.State.NULL)
                self.pipe.remove(e)
            self._build_video()
            return Gst.PadProbeReturn.REMOVE
        self.video_src.add_probe(Gst.PadProbeType.BLOCK_DOWNSTREAM, swap)
        return True

    def _probe_decode(self, dec):
        from gi.repository import Gst

        def into(pad, info):
            b = info.get_buffer()
            if b is not None:
                self.dec_in[b.pts] = time.monotonic()
                if len(self.dec_in) > 240:
                    self.dec_in.pop(next(iter(self.dec_in)))
            return Gst.PadProbeReturn.OK

        def out(pad, info):
            b = info.get_buffer()
            t = self.dec_in.pop(b.pts, None) if b is not None else None
            if t is not None and self.stats:
                self.stats.add_decode((time.monotonic() - t) * 1000.0)
            return Gst.PadProbeReturn.OK
        dec.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, into)
        dec.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, out)

    def _on_sample(self, sink):
        from gi.repository import Gst
        sample = sink.emit("pull-sample")
        if sample is not None:
            with self.lock:
                if self.sample is not None and self.stats:
                    self.stats.dropped += 1
                self.sample = sample
            self.events.put(("frame",))
        return Gst.FlowReturn.OK

    def take_frame(self):
        with self.lock:
            s, self.sample = self.sample, None
        return s

    # ---- data channels
    def _on_channel(self, el, ch):
        from gi.repository import GstWebRTC
        label = ch.get_property("label")
        if label == "pad":
            self.pad_ch = ch
        elif label == "ctl":
            self.ctl_ch = ch
            ch.connect("on-message-string", lambda c, text: self._on_ctl(text))
        ch.connect("on-open", lambda c: self.events.put(("open", label)))
        if ch.get_property("ready-state") == GstWebRTC.WebRTCDataChannelState.OPEN:
            self.events.put(("open", label))

    def _on_ctl(self, text):
        try:
            m = json.loads(text)
        except ValueError:
            return
        if isinstance(m, dict):
            self.events.put(("ctl", m))

    def send_pad(self, data):
        from gi.repository import GLib
        if self.pad_ch is not None:
            self.pad_ch.emit("send-data", GLib.Bytes.new(data))

    def send_ctl(self, obj):
        if self.ctl_ch is not None:
            self.ctl_ch.emit("send-string", json.dumps(obj))

    # ---- numbers and problems
    def ask_stats(self):
        """-> (bytes received, packets lost) of the video, from webrtcbin's statistics (0, 0 until known)."""
        from gi.repository import Gst
        p = Gst.Promise.new()
        self.wb.emit("get-stats", None, p)
        if p.wait() != Gst.PromiseResult.REPLIED:
            return 0, 0
        reply, best = p.get_reply(), (0, 0)
        for i in range(reply.n_fields()):
            s = reply.get_value(reply.nth_field_name(i))
            if not isinstance(s, Gst.Structure) or not s.has_field("type"):
                continue
            try:
                kind = s.get_value("type").value_nick
            except AttributeError:
                continue
            if kind == "inbound-rtp" and s.has_field("bytes-received"):
                got = (int(s.get_value("bytes-received")), int(s.get_value("packets-lost") or 0) if s.has_field("packets-lost") else 0)
                best = max(best, got)
        return best

    def poll_bus(self):
        """-> [(text, from the decoder?)] errors since the last call."""
        from gi.repository import Gst
        out = []
        while True:
            m = self.bus.pop_filtered(Gst.MessageType.ERROR)
            if m is None:
                return out
            err, dbg = m.parse_error()
            from_dec, el, top = False, m.src, None
            while el is not None:
                from_dec = from_dec or el is getattr(self, "decoder", None)
                top, el = el, (el.get_parent() if hasattr(el, "get_parent") else None)
            log("GStreamer error:", err.message, dbg)
            if top is self.pipe:        # (not from a decoder that was already swapped out)
                out.append((err.message, from_dec))

    def stop(self):
        from gi.repository import Gst
        if self.pipe:
            self.pipe.set_state(Gst.State.NULL)
            self.pipe = None


def available_elements():
    try:
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstWebRTC", "1.0")
        gi.require_version("GstSdp", "1.0")
        gi.require_version("GstApp", "1.0")
        gi.require_version("GstVideo", "1.0")
        from gi.repository import Gst
        Gst.init(None)
    except Exception:
        return None
    names = {n for n, _ in NEEDED} | set(DECODERS)
    return {n for n in names if Gst.ElementFactory.find(n)}


# ===================================================================== SDL: the window, keyboard and controllers
PAPER, INK = (228, 227, 223), (20, 20, 20)
CHIP = (228, 227, 223, 235)


def sdl_problem():
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        os.environ.setdefault("PYSDL2_DLL_PATH", sys._MEIPASS)     # the Windows player carries SDL2.dll next to it
    try:
        import sdl2  # noqa: F401
        import sdl2.sdlttf  # noqa: F401
    except Exception as e:
        return f"PySDL2 is missing ({str(e)[:80]}). Install: python3 -m pip install pysdl2 pysdl2-dll"
    return ""


class Window:
    """The player window. Draws the newest frame (YUV texture, scaled on the graphics card), the messages and the
    numbers; reads the keyboard and the first game controller."""

    def __init__(self, title, headless=False):
        if headless:
            os.environ["SDL_VIDEODRIVER"] = "dummy"
            os.environ["SDL_AUDIODRIVER"] = "dummy"
        import sdl2
        import sdl2.sdlttf as ttf
        self.sdl, self.ttf = sdl2, ttf
        sdl2.SDL_SetHint(sdl2.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b"1")   # the controller keeps working
        sdl2.SDL_SetHint(sdl2.SDL_HINT_RENDER_SCALE_QUALITY, b"1")
        sdl2.SDL_SetHint(b"SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR", b"1")       # less delay on X11 desktops
        if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO | sdl2.SDL_INIT_GAMECONTROLLER | sdl2.SDL_INIT_EVENTS) != 0:
            raise RuntimeError("couldn't open a window: " + sdl2.SDL_GetError().decode(errors="replace"))
        ttf.TTF_Init()
        self.win = sdl2.SDL_CreateWindow(title.encode(), sdl2.SDL_WINDOWPOS_CENTERED, sdl2.SDL_WINDOWPOS_CENTERED,
                                         1280, 720, sdl2.SDL_WINDOW_RESIZABLE | sdl2.SDL_WINDOW_ALLOW_HIGHDPI)
        if not self.win:
            raise RuntimeError("couldn't open a window: " + sdl2.SDL_GetError().decode(errors="replace"))
        self.ren = sdl2.SDL_CreateRenderer(self.win, -1, sdl2.SDL_RENDERER_ACCELERATED)
        if not self.ren:
            self.ren = sdl2.SDL_CreateRenderer(self.win, -1, sdl2.SDL_RENDERER_SOFTWARE)
        sdl2.SDL_SetRenderDrawBlendMode(self.ren, sdl2.SDL_BLENDMODE_BLEND)
        self.fonts = {}
        self.tex, self.tex_key = None, None
        self.pads = {}           # instance id -> SDL_GameController
        self.held = set()        # keyboard keys held (SDL names, lower case)
        self.full = False
        self.wake_type = sdl2.SDL_RegisterEvents(1)
        self.text_cache = {}

    # ---- waking up from other threads (a new frame, a message)
    def wake(self):
        ev = self.sdl.SDL_Event()
        ev.type = self.wake_type
        self.sdl.SDL_PushEvent(ctypes.byref(ev))

    # ---- input
    def events(self, timeout_ms):
        """Wait up to timeout_ms for something to happen; return the SDL events (as (kind, detail) pairs)."""
        sdl = self.sdl
        out, ev = [], sdl.SDL_Event()
        if sdl.SDL_WaitEventTimeout(ctypes.byref(ev), int(timeout_ms)):
            while True:
                out.append(self._event(ev))
                if not sdl.SDL_PollEvent(ctypes.byref(ev)):
                    break
        return [e for e in out if e]

    def _event(self, ev):
        sdl = self.sdl
        t = ev.type
        if t == sdl.SDL_QUIT:
            return ("quit", None)
        if t in (sdl.SDL_KEYDOWN, sdl.SDL_KEYUP):
            name = sdl.SDL_GetKeyName(ev.key.keysym.sym).decode(errors="replace").lower()
            down = t == sdl.SDL_KEYDOWN
            if down:
                self.held.add(name)
            else:
                self.held.discard(name)
            alt = bool(ev.key.keysym.mod & sdl.KMOD_ALT)
            return ("key", (name, down, bool(ev.key.repeat), alt))
        if t == sdl.SDL_CONTROLLERDEVICEADDED:
            gc = sdl.SDL_GameControllerOpen(ev.cdevice.which)
            if gc:
                self.pads[sdl.SDL_JoystickInstanceID(sdl.SDL_GameControllerGetJoystick(gc))] = gc
                return ("pad", sdl.SDL_GameControllerName(gc).decode(errors="replace") if sdl.SDL_GameControllerName(gc) else "controller")
        if t == sdl.SDL_CONTROLLERDEVICEREMOVED:
            gc = self.pads.pop(ev.cdevice.which, None)
            if gc:
                sdl.SDL_GameControllerClose(gc)
            return ("pad", None)
        if t == sdl.SDL_WINDOWEVENT and ev.window.event == sdl.SDL_WINDOWEVENT_FOCUS_LOST:
            self.held.clear()          # never leave a key stuck down when the window loses focus
            return ("focus", False)
        if t == sdl.SDL_MOUSEBUTTONDOWN:
            return ("click", (ev.button.x, ev.button.y))
        if t == sdl.SDL_MOUSEMOTION:
            return ("mouse", None)
        if t == self.wake_type:
            return ("wake", None)
        return None

    def controller(self):
        """The first controller's state, or None if none is plugged in."""
        sdl = self.sdl
        for gc in self.pads.values():
            buttons = {b: sdl.SDL_GameControllerGetButton(gc, b) for b in SDL_BUTTONS}
            axes = [sdl.SDL_GameControllerGetAxis(gc, a) for a in range(6)]
            return controller_state(buttons, axes)
        return None

    def toggle_full(self):
        self.full = not self.full
        self.sdl.SDL_SetWindowFullscreen(self.win, self.sdl.SDL_WINDOW_FULLSCREEN_DESKTOP if self.full else 0)
        self.sdl.SDL_ShowCursor(0 if self.full else 1)

    # ---- drawing
    def size(self):
        w, h = ctypes.c_int(), ctypes.c_int()
        self.sdl.SDL_GetRendererOutputSize(self.ren, ctypes.byref(w), ctypes.byref(h))
        return w.value, h.value

    def upload(self, sample):
        """Put a decoded frame (NV12 or I420) into the video texture. Returns (width, height)."""
        from gi.repository import GstVideo
        sdl = self.sdl
        caps = sample.get_caps()
        info = GstVideo.VideoInfo.new_from_caps(caps)
        w, h, fmt = info.width, info.height, info.finfo.name if hasattr(info, "finfo") else ""
        fmt = caps.get_structure(0).get_string("format") or fmt
        buf = sample.get_buffer()
        meta = GstVideo.buffer_get_video_meta(buf)
        offs = list(meta.offset) if meta else list(info.offset)
        strides = list(meta.stride) if meta else list(info.stride)
        key = (w, h, fmt)
        if self.tex_key != key:
            if self.tex:
                sdl.SDL_DestroyTexture(self.tex)
            pf = sdl.SDL_PIXELFORMAT_NV12 if fmt == "NV12" else sdl.SDL_PIXELFORMAT_IYUV
            self.tex = sdl.SDL_CreateTexture(self.ren, pf, sdl.SDL_TEXTUREACCESS_STREAMING, w, h)
            self.tex_key = key
        data = buf.extract_dup(0, buf.get_size())
        base = ctypes.cast(ctypes.c_char_p(data), ctypes.c_void_p).value
        plane = lambda i: ctypes.cast(ctypes.c_void_p(base + offs[i]), ctypes.POINTER(ctypes.c_ubyte))
        if fmt == "NV12":
            sdl.SDL_UpdateNVTexture(self.tex, None, plane(0), strides[0], plane(1), strides[1])
        else:
            sdl.SDL_UpdateYUVTexture(self.tex, None, plane(0), strides[0], plane(1), strides[1], plane(2), strides[2])
        self._keep = data      # the bytes must live until the texture has them (they do once Update returns)
        return w, h

    def font(self, size, bold=False):
        k = (size, bold)
        if k not in self.fonts:
            path = FONT.replace(".ttf", "-Bold.ttf") if bold else FONT
            self.fonts[k] = self.ttf.TTF_OpenFont(path.encode(), size)
        return self.fonts[k]

    def text(self, s, x, y, size=18, color=INK, bold=False, bg=None, pad=8):
        """Draw one line; returns its (w, h)."""
        sdl = self.sdl
        f = self.font(size, bold)
        if not f or not s:
            return 0, 0
        key = (s, size, color, bold)
        tex = self.text_cache.get(key)
        if tex is None:
            surf = self.ttf.TTF_RenderUTF8_Blended(f, s.encode(), sdl.SDL_Color(*color))
            if not surf:
                return 0, 0
            tex = (sdl.SDL_CreateTextureFromSurface(self.ren, surf), surf.contents.w, surf.contents.h)
            sdl.SDL_FreeSurface(surf)
            if len(self.text_cache) > 200:
                for t in self.text_cache.values():
                    sdl.SDL_DestroyTexture(t[0])
                self.text_cache.clear()
            self.text_cache[key] = tex
        t, w, h = tex
        if bg:
            self.rect(x - pad, y - pad // 2, w + 2 * pad, h + pad, bg)
        sdl.SDL_RenderCopy(self.ren, t, None, sdl.SDL_Rect(x, y, w, h))
        return w, h

    def wrap(self, s, size, width, bold=False):
        f = self.font(size, bold)
        words, lines, cur = s.split(), [], ""
        for word in words:
            test = (cur + " " + word).strip()
            w, h = ctypes.c_int(), ctypes.c_int()
            self.ttf.TTF_SizeUTF8(f, test.encode(), ctypes.byref(w), ctypes.byref(h))
            if w.value > width and cur:
                lines.append(cur)
                cur = word
            else:
                cur = test
        return lines + ([cur] if cur else [])

    def rect(self, x, y, w, h, color):
        sdl = self.sdl
        sdl.SDL_SetRenderDrawColor(self.ren, *color[:3], color[3] if len(color) > 3 else 255)
        sdl.SDL_RenderFillRect(self.ren, sdl.SDL_Rect(int(x), int(y), int(w), int(h)))

    def draw(self, ui, shot=None):
        """ui: {"video": (w, h) or None, "message", "title", "hint", "overlay": [lines] or None, "slots": (n, [picks])}."""
        sdl = self.sdl
        W, H = self.size()
        sdl.SDL_SetRenderDrawColor(self.ren, 0, 0, 0, 255)
        sdl.SDL_RenderClear(self.ren)
        chips = []
        if ui.get("video") and self.tex:
            vw, vh = ui["video"]
            scale = min(W / vw, H / vh)
            dw, dh = int(vw * scale), int(vh * scale)
            sdl.SDL_RenderCopy(self.ren, self.tex, None, sdl.SDL_Rect((W - dw) // 2, (H - dh) // 2, dw, dh))
        if ui.get("message"):
            # the paper screen: what is happening, or why it stopped, in plain words
            alpha = 235 if ui.get("video") else 255
            self.rect(0, 0, W, H, PAPER + (alpha,))
            self.text("Retro x Netplay", 28, 26, 16, INK, bold=True)
            if ui.get("title"):
                self.text(ui["title"], 28, 52, 16, INK)
            size = 24 if W >= 900 else 18
            lines = self.wrap(ui["message"], size, min(W - 56, 1100))
            y = H - 60 - len(lines) * (size + 10)
            for ln in lines:
                self.text(ln, 28, y, size, INK)
                y += size + 10
            if ui.get("after"):
                self.text(ui["after"], 28, H - 44, 15, (74, 74, 72))
        if ui.get("overlay"):
            y = 14
            for ln in ui["overlay"]:
                self.text(ln, 18, y, 15, INK, bg=CHIP)
                y += 26
        slots = ui.get("slots")
        if slots and slots[1] and not ui.get("message"):
            n, picks = slots
            x = W - 14
            for p in reversed(picks):
                label = f"P{p}"
                w, h = self.text(label, -1000, -1000, 15)   # measure
                x -= w + 22
                on = p == n
                self.text(label, x, 14, 15, PAPER if on else INK, bold=on, bg=INK + (255,) if on else CHIP, pad=10)
                chips.append((p, x - 10, 10, w + 20, h + 10))
            self.text("player (press 2-4):", x - 220, 14, 14, PAPER)
        if ui.get("hint"):
            self.text(ui["hint"], 18, H - 40, 15, INK, bg=CHIP)
        if shot:
            self.screenshot(shot)
        sdl.SDL_RenderPresent(self.ren)
        return chips

    def screenshot(self, path):
        """Save what the window shows as a .bmp (automatic tests)."""
        sdl = self.sdl
        w, h = self.size()
        surf = sdl.SDL_CreateRGBSurfaceWithFormat(0, w, h, 32, sdl.SDL_PIXELFORMAT_ARGB8888)
        sdl.SDL_RenderReadPixels(self.ren, None, sdl.SDL_PIXELFORMAT_ARGB8888, surf.contents.pixels, surf.contents.pitch)
        sdl.SDL_SaveBMP(surf, path.encode())
        sdl.SDL_FreeSurface(surf)

    def close(self):
        for gc in self.pads.values():
            self.sdl.SDL_GameControllerClose(gc)
        self.sdl.SDL_Quit()


# ===================================================================== the player
TEST_INPUT = [  # (seconds, SDL buttons held, axes): what --test-input presses on a pretend controller
    (0.6, {0: 1}, [0, 0, 0, 0, -32768, -32768]),                         # A  (a pretend pad rests its triggers at -32768)
    (0.6, {0: 1, 13: 1}, [0, 0, 0, 0, -32768, -32768]),                  # A + d-pad left
    (0.6, {6: 1, 12: 1}, [16384, -32768, 0, 0, -32768, 32767]),          # Start + d-pad down, stick, right trigger
    (0.8, {}, [0, 0, 0, 0, -32768, -32768]),                             # everything let go
]
HINT = "Esc twice: leave   F11 / Alt+Enter: full screen   F2: numbers"


class Player:
    def __init__(self, a):
        self.a = a
        self.events = queue.Queue()
        self.media = self.sig = self.win = None
        self.message = "Connecting to your friend's game..."
        self.ended = ""             # why it ended ('' while running)
        self.error = False
        self.playing = False
        self.heard = False
        self.video = None
        self.relay = False
        self.slot, self.picks, self.host_stats = 0, [], {}
        self.stats = Stats(a.jitter_ms)
        self.sender = InputSender()
        self.overlay = a.stats_overlay
        self.leave_at = 0.0
        self.hint_until = 0.0
        self.opened = set()
        self.start = time.monotonic()
        self.video_at = None
        self.vpad = None
        self.shot = False
        self.quit = False

    # ---- set-up
    def connect(self):
        """(runs on a thread) the stream connection, then the media."""
        import stream_signal
        host, port = stream_signal.parse_address(self.a.mm)
        self.sig = stream_signal.TcpSignaling(host, port, self.a.key, on_msg=lambda m: self.post(("sig", m)),
                                              on_end=lambda why: self.post(("sig_end", why)))
        try:
            cfg = self.sig.connect()
        except stream_signal.SignalError as e:
            text = ("This stream has ended (the link is old, or your friend stopped it). Press Join again."
                    if e.why == "bad_key" else str(e))
            return self.post(("fatal", text))
        self.post(("connected", cfg))

    def post(self, ev):
        self.events.put(ev)
        if self.win:
            self.win.wake()

    def begin_media(self, cfg):
        import stream_signal
        if self.a.no_relay:
            cfg = {**cfg, "relay": False, "iceServers": stream_signal.without_relay(cfg.get("iceServers"))}
        self.relay = bool(cfg.get("relay"))
        try:
            self.media = Media(cfg.get("iceServers", []), self, self.a.jitter_ms, self.a.decoder,
                               audio=not self.a.no_audio, headless=self.a.headless, relay_only=self.a.relay_only)
            self.media.stats = self.stats
            self.media.start()
        except Exception as e:
            return self.finish(f"The player couldn't start: {e}", error=True)
        self.sig.start()
        self.sig.send({"type": "hello"})
        self.say("Waiting for your friend's computer...")

    def put(self, ev):            # Media calls this (as if it were a queue)
        self.post(ev)

    # ---- what happens
    def say(self, text):
        if not self.playing and not self.ended:
            self.message = text

    def finish(self, text, error=False):
        if self.ended:
            return
        self.ended, self.error = text, error
        self.message = text
        self.playing = False
        log("ended:", text)
        if self.media:
            self.media.stop()

    def handle(self, ev):
        kind = ev[0]
        if kind == "connected":
            self.begin_media(ev[1])
        elif kind == "fatal":
            self.finish(ev[1], error=True)
        elif kind == "sig":
            self.on_signal(ev[1])
        elif kind == "sig_end":
            if not self.ended:
                self.finish(end_text(ev[1], self.relay), error=ev[1] not in ("session closed", "session ended"))
        elif kind == "answer":
            self.sig.send({"type": "answer", "sdp": ev[1]})
        elif kind == "ice":
            self.sig.send({"type": "ice", "candidate": ev[2], "sdpMLineIndex": ev[1]})
        elif kind in ("state", "ice_state"):
            self.sig.send({"type": "status", "text": ("connection " if kind == "state" else "ice ") + ev[1]})
            log(kind, ev[1])
            if ev[1] == "failed" and not self.ended:
                self.finish(end_text("ice failed", self.relay), error=True)
            elif kind == "state" and ev[1] == "connected":
                self.say("Connected. The picture is on its way...")
        elif kind == "open" and ev[1] not in self.opened:     # (a channel can report "open" twice)
            self.opened.add(ev[1])
            if ev[1] == "ctl":
                self.media.send_ctl({"t": "hi", "ua": f"Retro-Netplay player ({platform.system()})"})
            if ev[1] == "pad" and self.a.test_input:
                self.start_test_input()
        elif kind == "ctl":
            self.on_ctl(ev[1])
        elif kind == "frame":
            pass
        elif kind == "decoder":
            self.decoder = ev[1]
        elif kind == "error":
            self.finish(f"The player couldn't show the stream: {ev[1]}", error=True)

    def on_signal(self, m):
        t = m.get("type")
        if t == "offer" and isinstance(m.get("sdp"), str) and self.media:
            self.heard = True
            self.say("Connecting the video...")
            self.media.set_offer(m["sdp"])
        elif t == "ice" and isinstance(m.get("candidate"), str) and self.media:
            self.media.add_ice(m.get("sdpMLineIndex") or 0, m["candidate"])
        elif t == "status" and isinstance(m.get("text"), str):
            self.heard = True
            self.say(m["text"][:300])
            log("friend's computer:", m["text"][:300])
        elif t == "bye":
            self.finish(bye_text(m.get("why")), error=bool(m.get("why")))

    def on_ctl(self, m):
        t = m.get("t")
        if t == "pong" and isinstance(m.get("ts"), (int, float)):
            self.stats.rtt = time.monotonic() * 1000 - m["ts"]
        elif t == "slot" and isinstance(m.get("n"), int):
            self.slot = m["n"]
            self.picks = [p for p in (m.get("pick") or []) if isinstance(p, int) and 1 <= p <= 8][:6]
            log(f"you are Player {self.slot}")
            if self.a.want_slot and self.a.want_slot in self.picks and self.a.want_slot != self.slot:
                self.want(self.a.want_slot)
                self.a.want_slot = 0
        elif t == "host" and isinstance(m.get("s"), dict):
            self.host_stats = m["s"]

    def want(self, n):
        if n in self.picks and self.media:
            self.media.send_ctl({"t": "want_slot", "n": n})

    # ---- the pretend controller (automatic tests drive the real SDL controller code with it)
    def start_test_input(self):
        sdl = self.win.sdl
        i = sdl.SDL_JoystickAttachVirtual(sdl.SDL_JOYSTICK_TYPE_GAMECONTROLLER, 6, 15, 0)
        self.vpad = {"joy": sdl.SDL_JoystickOpen(i), "t0": time.monotonic(), "step": -1}

    def drive_test_input(self):
        if not self.vpad:
            return
        sdl, j = self.win.sdl, self.vpad["joy"]
        t, acc = time.monotonic() - self.vpad["t0"], 0.0
        for n, (dur, buttons, axes) in enumerate(TEST_INPUT):
            acc += dur
            if t < acc:
                if n != self.vpad["step"]:
                    self.vpad["step"] = n
                    for b in range(15):
                        sdl.SDL_JoystickSetVirtualButton(j, b, buttons.get(b, 0))
                    for k, v in enumerate(axes):
                        sdl.SDL_JoystickSetVirtualAxis(j, k, v)
                    log("test input step", n)
                return
        if self.vpad["step"] != "done":
            self.vpad["step"] = "done"
            log("test input done")

    # ---- the loop
    def run(self):
        a = self.a
        title = f"Retro-Netplay - playing with {a.name}" if a.name else "Retro-Netplay"
        problem = sdl_problem()
        if problem:
            print(f"GUEST ENDED: {problem}", flush=True)
            return 1
        try:
            self.win = Window(title, a.headless)
        except Exception as e:
            print(f"GUEST ENDED: {e}", flush=True)
            return 1
        if available_elements() is None:
            self.finish("Joining needs GStreamer on this computer, and it isn't installed. "
                        + " ".join(check_lines(None)[1:2]).strip(), error=True)
        else:
            threading.Thread(target=self.connect, daemon=True).start()
        if a.fullscreen and not a.headless:
            self.win.toggle_full()
        last_tick = last_csv = 0.0
        chips, quit_ = [], False
        try:
            while not quit_ and not self.quit:
                now = time.monotonic()
                for kind, d in self.win.events(4 if self.media and not self.ended else 50):
                    if kind == "quit":
                        quit_ = True
                    elif kind == "key":
                        quit_ = self.on_key(*d) or quit_
                    elif kind == "click":
                        for p, x, y, w, h in chips:
                            if x <= d[0] <= x + w and y <= d[1] <= y + h:
                                self.want(p)
                    elif kind == "mouse" and self.playing:
                        self.hint_until = max(self.hint_until, now + 3)
                    elif kind == "pad" and d:
                        log("controller:", d)
                while True:
                    try:
                        self.handle(self.events.get_nowait())
                    except queue.Empty:
                        break
                if self.ended and (a.headless or a.seconds):
                    break
                if self.media and not self.ended:
                    for text, from_dec in self.media.poll_bus():
                        if not (from_dec and self.media.next_decoder()):
                            self.finish(f"The player couldn't show the stream: {text}", error=True)
                    self.drive_test_input()
                    self.send_input()
                    frame = self.media.take_frame() if self.media else None
                    if frame is not None:
                        self.video = self.win.upload(frame)
                        self.stats.w, self.stats.h = self.video
                        self.stats.frames += 1
                        if not self.playing:
                            self.playing, self.message, self.video_at = True, "", now
                            self.hint_until = now + 6
                            log("video:", f"{self.video[0]}x{self.video[1]}")
                    if now - last_tick >= 1.0:
                        last_tick = now
                        self.tick(now, last_csv)
                        if now - last_csv >= 2.0:
                            last_csv = now
                    if not self.heard and not self.playing and now - self.start > 20:
                        self.say(end_text("no host"))
                    if not self.playing and self.heard and now - self.start > 45:
                        self.say(end_text("no video", self.relay))
                    if a.seconds and not self.video_at and now - self.start > 30:     # (tests: never wait forever)
                        self.finish(end_text("no video" if self.heard else "no host", self.relay), error=True)
                    if a.seconds and self.video_at and now - self.video_at > a.seconds:
                        log("test time is up")
                        quit_ = True
                shot = None
                if a.screenshot and not self.shot and (self.ended or (self.video_at and now - self.video_at > 2.5)):
                    shot, self.shot = a.screenshot, True
                chips = self.win.draw(self.ui(now), shot)
        finally:
            self.leave()
        why = self.ended or "you left"
        print(f"GUEST ENDED: {why}", flush=True)       # the app shows this line when the player closes
        return 1 if self.error else 0

    def on_key(self, name, down, repeat, alt):
        """-> True to leave."""
        now = time.monotonic()
        if not down or repeat:
            return False
        if name == "f11" or (name == "return" and alt):
            self.win.toggle_full()
            self.win.held.discard("return")
        elif name == "f2":
            self.overlay = not self.overlay
        elif name == "escape":
            if self.ended or now - self.leave_at < 3:
                return True
            self.leave_at = now
            self.hint_until = now + 3
        elif name in ("2", "3", "4", "5", "6", "7", "8") and self.picks:
            self.want(int(name))
        return False

    def send_input(self):
        if "pad" not in self.opened:
            return
        keys = keyboard_buttons(self.win.held)
        st = combine(self.win.controller(), keys)
        data = self.sender.next(st)
        if data:
            self.media.send_pad(data)

    def tick(self, now, last_csv):
        """Once a second: ping, numbers to the host, the stats file every 2 s."""
        if "ctl" not in self.opened:
            return
        self.media.send_ctl({"t": "ping", "ts": time.monotonic() * 1000})
        try:
            bytes_rx, lost = self.media.ask_stats()
        except Exception:
            bytes_rx, lost = 0, 0
        s = self.stats.update(now, bytes_rx, lost)
        if self.playing:
            self.media.send_ctl({"t": "stats", "s": s})
            print("STATS " + json.dumps({**s, "inputs": self.sender.sent, "slot": self.slot}), flush=True)
            if now - last_csv >= 2.0:
                write_stats_row(self.a.stats_file, s, self.host_stats)

    def ui(self, now):
        ui = {"video": self.video if self.playing or (self.ended and self.video) else None,
              "message": self.message, "title": f"playing with {self.a.name}" if self.a.name else ""}
        if self.ended:
            ui["after"] = "Press Esc or close this window."
        if self.playing and now < self.hint_until:
            ui["hint"] = ("Press Esc again to leave" if now - self.leave_at < 3 else HINT)
        if self.overlay and self.playing:
            ui["overlay"] = overlay_lines(self.stats.s, self.host_stats, self.sender.sent)
        ui["slots"] = (self.slot, self.picks)
        return ui

    def leave(self):
        if self.sig:
            if not self.ended:
                self.sig.send({"type": "bye"})          # tell the host we left (no reason: it was on purpose)
            self.sig.flush(1.0)
            self.sig.stop()
        if self.media:
            self.media.stop()
        if self.win:
            self.win.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Retro-Netplay player (joins a friend's built-in stream)")
    ap.add_argument("--mm", help="matchmaker address, e.g. 1.2.3.4:7777")
    ap.add_argument("--key", help="this player's session key from the match")
    ap.add_argument("--name", default="", help="the friend's name, for the window title")
    ap.add_argument("--check", action="store_true", help="say what this computer can do and exit")
    ap.add_argument("--want-slot", type=int, default=0, help="ask to be this player if the host allows choosing")
    ap.add_argument("--jitter-ms", type=int, default=15, help="network jitter buffer (small = less delay)")
    ap.add_argument("--decoder", default="auto", help="auto, or a GStreamer H.264 decoder such as avdec_h264")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-relay", action="store_true", help="never send the stream through the matchmaker's relay server")
    ap.add_argument("--stats-overlay", action="store_true", help="show the numbers from the start (F2 toggles)")
    ap.add_argument("--stats-file", default=STATS_CSV)
    # for the automatic tests
    ap.add_argument("--headless", action="store_true", help="no real window or sound (tests)")
    ap.add_argument("--seconds", type=float, default=0, help="leave after this many seconds of video (tests)")
    ap.add_argument("--test-input", action="store_true", help="press buttons on a pretend controller (tests)")
    ap.add_argument("--relay-only", action="store_true", help="connect only through the relay server (tests)")
    ap.add_argument("--screenshot", help="save the window as a .bmp after a few seconds of video, or at the end (tests)")
    a = ap.parse_args(argv)
    if a.check:
        lines = check_lines(available_elements(), sdl_problem())
        print("\n".join(lines))
        return 0 if not any(l.startswith("[X") for l in lines) else 1
    if not a.mm or not a.key:
        ap.error("--mm and --key are required (or use --check)")
    player = Player(a)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: setattr(player, "quit", True))   # the app's End session: leave politely
    return player.run()


if __name__ == "__main__":
    sys.exit(main())
