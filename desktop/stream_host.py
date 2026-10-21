"""Host side of the built-in streamer (Linux).

Captures a window/screen + game audio, encodes it with the fastest encoder available, and sends it
to ONE guest's browser over WebRTC. The guest's controller comes back over a data channel and is
fed into a virtual gamepad. The matchmaker only relays the setup messages.

  python3 stream_host.py --mm HOST:PORT --key KEY                 (the app starts this for you)
  python3 stream_host.py --server http://HOST:PORT --key KEY      (DEPRECATED web way, for older matchmakers)
  python3 stream_host.py --doctor                                  (checks this computer, no network)

The orchestration (Session) and all packing/choosing logic are plain Python and unit-tested.
GstBackend (the real capture/encode/WebRTC) needs GStreamer and has NOT been run in the sandbox
this was written in; run --doctor first and send back anything it flags.
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

import uinput_pad

STATS_CSV = os.path.expanduser("~/.netplay-matchmaker/stream-stats.csv")


def log(*a):
    print("[stream]", *a, flush=True)


# ===================================================================== choosing an encoder
# name -> properties (value lists = try in order, first one this GStreamer knows wins)
def encoder_table(kbps, fps):
    gop = max(fps * 2, 30)
    return [
        ("nvh264enc", {"preset": ("low-latency-hq", "p1", "low-latency"), "tune": ("ultra-low-latency",),
                       "zerolatency": True, "rc-mode": ("cbr-ld-hq", "cbr"), "bitrate": kbps,
                       "gop-size": gop, "bframes": 0, "aud": False}),
        ("vah264lpenc", {"bitrate": kbps, "rate-control": "cbr", "key-int-max": gop, "b-frames": 0,
                         "target-usage": 7, "cpb-size": kbps // 2}),
        ("vah264enc", {"bitrate": kbps, "rate-control": "cbr", "key-int-max": gop, "b-frames": 0,
                       "target-usage": 7, "cpb-size": kbps // 2}),
        ("vaapih264enc", {"bitrate": kbps, "rate-control": "cbr", "keyframe-period": gop, "max-bframes": 0,
                          "tune": ("low-power", "high-compression")}),
        ("x264enc", {"tune": "zerolatency", "speed-preset": "ultrafast", "bitrate": kbps, "key-int-max": gop,
                     "bframes": 0, "sliced-threads": True, "byte-stream": True}),
    ]


def pick_encoder(available, prefer="auto", kbps=8000, fps=60):
    """First usable encoder (hardware before software), or the one the user asked for."""
    table = encoder_table(kbps, fps)
    for name, props in table:
        if prefer in ("auto", name) and name in available:
            return name, props
    return None, None


def ice_to_gst(servers):
    """WebRTC-style iceServers list -> (stun uri for webrtcbin or None, [turn uris])."""
    stun, turns = None, []
    for s in servers or []:
        urls = s.get("urls", [])
        urls = [urls] if isinstance(urls, str) else urls
        for u in urls:
            if u.startswith("stun:") and stun is None:
                stun = "stun://" + u[5:]
            elif u.startswith(("turn:", "turns:")):
                scheme, rest = u.split(":", 1)
                cred = ""
                if s.get("username"):
                    cred = f"{quote(s['username'], safe='')}:{quote(s.get('credential', ''), safe='')}@"
                turns.append(f"{scheme}://{cred}{rest.split('?')[0]}")
    return stun, turns


# ===================================================================== signaling over HTTP (DEPRECATED)
class Signaling:
    """Long-polls the matchmaker's web port for messages and posts ours. Runs on its own threads.

    DEPRECATED: only for a matchmaker too old to have the TCP stream connection (stream_signal.TcpSignaling,
    which the app uses whenever the match says sig "tcp"). Remove with the web guest page."""

    def __init__(self, base, key, on_msg, on_end):
        self.base, self.key, self.on_msg, self.on_end = base.rstrip("/"), key, on_msg, on_end
        self.out, self.stop_flag = queue.Queue(), threading.Event()

    def _url(self, path):
        return f"{self.base}{path}{'&' if '?' in path else '?'}t={quote(self.key)}"

    def get_json(self, path, timeout=35):
        with urllib.request.urlopen(self._url(path), timeout=timeout) as r:
            return json.load(r)

    def start(self):
        threading.Thread(target=self._poll, daemon=True).start()
        threading.Thread(target=self._send_loop, daemon=True).start()

    def send(self, msg):
        self.out.put(msg)

    def stop(self):
        self.stop_flag.set()
        self.out.put(None)

    def _poll(self):
        fails = 0
        while not self.stop_flag.is_set():
            try:
                res = self.get_json("/sig?wait=20")
                fails = 0
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return self.on_end("session ended")
                fails += 1
            except (urllib.error.URLError, OSError, ValueError):
                fails += 1
            else:
                for m in res.get("msgs", []):
                    self.on_msg(m)
                if res.get("closed") and not res.get("msgs"):
                    return self.on_end("session closed")
                continue
            if fails > 20:
                return self.on_end("lost contact with the matchmaker")
            time.sleep(min(0.3 * fails, 3))

    def _send_loop(self):
        while True:
            msg = self.out.get()
            if msg is None:
                return
            req = urllib.request.Request(self._url("/sig"), data=json.dumps(msg).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            for attempt in range(3):
                try:
                    urllib.request.urlopen(req, timeout=10).read()
                    break
                except (urllib.error.URLError, OSError):
                    time.sleep(0.3 * (attempt + 1))


# ===================================================================== host-side timing
class HostTimer:
    """Follows each video frame through the host's pipeline and reports how long every stage took.

    Stages, in order: cap (frame leaves the capture source), enc_in (reaches the encoder),
    enc_out (encoded), pay (packetised, about to be sent). Frames are matched by their timestamp.
    Pure Python so it can be tested without GStreamer; the GStreamer pad probes just call mark().
    """
    STAGES = ("cap", "enc_in", "enc_out", "pay")
    # name -> (from stage, to stage); "total" is the whole host pipeline
    SPANS = {"convertMs": ("cap", "enc_in"), "encodeMs": ("enc_in", "enc_out"),
             "payMs": ("enc_out", "pay"), "totalMs": ("cap", "pay")}

    def __init__(self, window=180, max_pending=240):
        self.window, self.max_pending = window, max_pending
        self.pending, self.order = {}, []
        self.samples = {k: [] for k in list(self.SPANS) + ["captureAgeMs"]}
        self.frames = 0
        self.lock = threading.Lock()

    def mark(self, stage, pts, now, age_ms=None):
        """now = time.monotonic() seconds. age_ms (cap stage only) = how old the frame already was."""
        with self.lock:
            marks = self.pending.get(pts)
            if marks is None:
                if stage != "cap":
                    return              # never saw it enter (dropped or restarted): ignore
                marks = self.pending[pts] = {}
                self.order.append(pts)
                if len(self.order) > self.max_pending:
                    self.pending.pop(self.order.pop(0), None)
            marks[stage] = now
            if age_ms is not None:
                self._add("captureAgeMs", max(age_ms, 0.0))
            if stage == "pay":
                self.frames += 1
                for name, (a, b) in self.SPANS.items():
                    if a in marks and b in marks:
                        self._add(name, (marks[b] - marks[a]) * 1000.0)
                self.pending.pop(pts, None)
                try:
                    self.order.remove(pts)
                except ValueError:
                    pass

    def _add(self, name, v):
        lst = self.samples[name]
        lst.append(v)
        if len(lst) > self.window:
            del lst[0]

    def summary(self):
        """{'encodeMs': mean, 'encodeP95': ..., ...} over the recent window; {} until frames have flowed."""
        with self.lock:
            out = {}
            for name, lst in self.samples.items():
                if lst:
                    srt = sorted(lst)
                    out[name] = round(sum(lst) / len(lst), 2)
                    out[name.replace("Ms", "P95")] = round(srt[min(len(srt) - 1, int(len(srt) * 0.95))], 2)
            if out:
                out["frames"] = self.frames
            return out


# ===================================================================== the session logic
def plain_reason(why):
    """Why the stream ended, in words for the friend's screen ('' when they left themselves)."""
    w = str(why)
    if w in ("guest left", "session ended", "session closed", "interrupted"):
        return ""
    if w.startswith("connection"):
        return ("the video connection between the two devices failed. Both need to be on the same Wi-Fi "
                "(not a guest network), or the computer's router is blocking it")
    if w.startswith("pipeline error"):
        detail = w[len("pipeline error"):].lstrip(": ").strip().rstrip(".")
        return ("capturing or encoding the game failed on the computer"
                + (f": {detail}" if detail else "") + " (details in its stream-host.log)")
    if w.startswith("couldn't start: "):
        return w[len("couldn't start: "):]
    if w == "lost contact with the matchmaker":
        return "the computer lost contact with the matchmaker"
    return w


class Session:
    """One guest. Backend does the media; this decides what to do with each message."""

    def __init__(self, signal, backend_factory, pad, slot=2, stats_path=STATS_CSV, clock=time.monotonic,
                 pick=None, pad_factory=None):
        self.signal, self.backend_factory, self.pad, self.slot = signal, backend_factory, pad, slot
        # a friend with the "pick a controller slot" privilege may choose one of these players
        self.pick, self.pad_factory = (list(pick) if pick and pad_factory else None), pad_factory
        self.backend, self.last_seq, self.last_packet, self.ended = None, None, 0.0, threading.Event()
        self.stats_path, self.clock, self.packets = stats_path, clock, 0
        self.host_stats = {}
        self._stats_t = 0.0

    # ---- from the guest, via signaling
    def on_signal(self, m):
        t = m.get("type")
        if t == "hello":
            self.restart_backend()
        elif t == "answer" and self.backend and isinstance(m.get("sdp"), str):
            self.backend.set_answer(m["sdp"])
        elif t == "ice" and self.backend and isinstance(m.get("candidate"), str):
            self.backend.add_ice(int(m.get("sdpMLineIndex", 0)), m["candidate"])
        elif t == "bye":
            self.end("guest left")
        elif t == "status":
            log("friend's screen:", str(m.get("text", ""))[:200])     # their side of the connection, for the log

    def status(self, text):
        """Tell the friend's screen what the computer is doing (so they never stare at 'Connecting...' blindly)."""
        log(text)
        self.signal.send({"type": "status", "text": str(text)[:300]})

    def restart_backend(self):
        if self.backend:
            self.backend.stop()
        self.last_seq = None
        self.pad.release_all()
        self.status("Your friend's computer is starting the stream...")
        self.backend = self.backend_factory(self)
        try:
            self.backend.start()
        except Exception as e:                  # say why on both screens instead of hanging
            self.backend = None
            self.status(f"The stream couldn't start on your friend's computer: {e}")
            print(f"STREAM ERROR: {e}", flush=True)
            self.end(f"couldn't start: {e}")

    # ---- from the backend
    def on_offer(self, sdp):
        self.signal.send({"type": "offer", "sdp": sdp})

    def on_ice(self, index, candidate):
        self.signal.send({"type": "ice", "candidate": candidate, "sdpMLineIndex": index})

    def on_pad(self, data):
        st = uinput_pad.parse_packet(data)
        if st is None:
            return
        if self.last_seq is not None and self.clock() - self.last_packet > 1.0:
            self.last_seq = None    # after a long silence, trust whatever number comes next
        if self.last_seq is not None:
            ahead = (st["seq"] - self.last_seq) & 255
            if ahead == 0 or ahead > 128:    # duplicate or older than what we've applied
                return
        self.last_seq, self.last_packet = st["seq"], self.clock()
        self.packets += 1
        self.pad.apply(st)

    def on_ctl(self, m):
        t = m.get("t")
        if t == "ping" and self.backend:
            self.backend.send_ctl({"t": "pong", "ts": m.get("ts")})
        elif t == "hi" and self.backend:
            self.send_slot()
        elif t == "want_slot" and self.backend and self.pick:
            n = m.get("n")
            if isinstance(n, int) and not isinstance(n, bool) and n in self.pick and n != self.slot:
                self.pad.release_all()
                self.pad.close()
                self.pad = self.pad_factory(n)
                self.slot = n
                log(f"guest switched to Player {n}")
            self.send_slot()
        elif t == "stats" and isinstance(m.get("s"), dict):
            self.record_stats(m["s"])

    def send_slot(self):
        msg = {"t": "slot", "n": self.slot}
        if self.pick:
            msg["pick"] = self.pick
        self.backend.send_ctl(msg)

    def on_state(self, state):
        log("connection:", state)
        if state in ("failed", "closed"):
            self.end(f"connection {state}")
        elif state == "disconnected":
            self.pad.release_all()      # never leave a button stuck down

    def record_stats(self, s):
        now = self.clock()
        if now - self._stats_t < 2:
            return
        self._stats_t = now
        keys = ["rttMs", "jitterBufMs", "decodeMs", "addedMs", "fps", "kbps", "lost", "dropped", "w", "h"]
        hkeys = ["captureAgeMs", "convertMs", "encodeMs", "encodeP95", "payMs", "totalMs"]
        row = [time.strftime("%H:%M:%S")] + [str(s.get(k, "")) for k in keys] + [str(self.host_stats.get(k, "")) for k in hkeys]
        keys = keys + hkeys
        try:
            os.makedirs(os.path.dirname(self.stats_path), exist_ok=True)
            new = not os.path.exists(self.stats_path)
            with open(self.stats_path, "a") as f:
                if new:
                    f.write(",".join(["time"] + keys) + "\n")
                f.write(",".join(row) + "\n")
        except OSError:
            pass
        h = self.host_stats
        if h:
            log("host spends: capture age %s ms, convert %s ms, encode %s ms (p95 %s), pack %s ms, total %s ms" % (
                h.get("captureAgeMs"), h.get("convertMs"), h.get("encodeMs"), h.get("encodeP95"), h.get("payMs"), h.get("totalMs")))
        log("guest sees: rtt %s ms, jitter buffer %s ms, decode %s ms, %s fps, %s kbps, lost %s" % (
            s.get("rttMs"), s.get("jitterBufMs"), s.get("decodeMs"), s.get("fps"), s.get("kbps"), s.get("lost")))

    def watchdog_tick(self, max_silence=0.5):
        """If the guest's page froze with a button held, let go. Guests send a heartbeat every 50 ms."""
        if self.pad.state["buttons"] or any(self.pad.state["ax"]) or self.pad.state["lt"] or self.pad.state["rt"]:
            if self.clock() - self.last_packet > max_silence:
                self.pad.release_all()
                return True
        return False

    def end(self, why):
        if self.ended.is_set():
            return
        log("ending:", why)
        self.ended.set()
        self.pad.release_all()
        if self.backend:
            self.backend.stop()
        self.why = why
        self.signal.send({"type": "bye", "why": plain_reason(why)})


# ===================================================================== GStreamer backend
class GstBackend:
    """capture -> encode -> webrtcbin, plus the 'pad' and 'ctl' data channels."""

    def __init__(self, session, opts, ice_servers):
        self.s, self.o, self.ice = session, opts, ice_servers
        self.pipe = self.wb = self.pad_ch = self.ctl_ch = None
        self.offered = self.playing = False
        self.keepalive = []
        self.timer = HostTimer() if opts.get("timing", True) else None

    # ---- helpers
    def _mk(self, factory, props=None, name=None):
        from gi.repository import Gst
        el = Gst.ElementFactory.make(factory, name)
        if el is None:
            raise RuntimeError(f"GStreamer element '{factory}' is missing. Run --doctor for what to install.")
        for k, v in (props or {}).items():
            self._set(el, k, v)
        return el

    @staticmethod
    def _set(el, key, value):
        from gi.repository import Gst
        pspec = el.find_property(key)
        if pspec is None:
            return False                         # this GStreamer version doesn't have it: skip quietly
        choices = value if isinstance(value, tuple) else (value,)
        if len(choices) > 1:                     # enum with alternatives: pick the first the encoder knows
            try:
                nicks = {v.value_nick for v in type(el.get_property(key)).__enum_values__.values()}
                choices = tuple(c for c in choices if c in nicks) or choices[:1]
            except Exception:
                pass
        v = choices[0]
        Gst.util_set_object_arg(el, key, ("true" if v else "false") if isinstance(v, bool) else str(v))
        return True

    def _caps(self, text):
        from gi.repository import Gst
        return self._mk("capsfilter", {"caps": Gst.Caps.from_string(text)})

    # ---- pipeline
    def start(self):
        from gi.repository import Gst, GstWebRTC
        o = self.o
        self.pipe = Gst.Pipeline.new("netplay")
        self.wb = self._mk("webrtcbin", {"bundle-policy": "max-bundle"}, "wb")
        stun, turns = ice_to_gst(self.ice)
        if stun:
            self.wb.set_property("stun-server", stun)
        for t in turns:
            self.wb.emit("add-turn-server", t)
        self.pipe.add(self.wb)

        # ---- video
        chain = []
        if o["capture"] == "portal":
            import portal_capture
            if hasattr(self.s, "status"):
                self.s.status("Waiting for your friend to choose the game window on their computer "
                              "(a 'share your screen' box opens there)...")
            slow = (lambda: self.s.status("Still waiting for the 'share your screen' box on your friend's computer. "
                                          "It may be hidden behind the game: they can press Alt+Tab to find it.")
                    ) if hasattr(self.s, "status") else None
            try:
                fd, node = portal_capture.request_screencast(timeout=60, on_slow=slow, progress=lambda t: log(t))
            except RuntimeError as e:
                if "in time" not in str(e):
                    raise
                # a box stuck from an earlier try blocks new ones (KDE shows one at a time): reset and ask again
                if hasattr(self.s, "status"):
                    self.s.status("Resetting screen sharing on your friend's computer and asking again...")
                restart_portal()
                fd, node = portal_capture.request_screencast(timeout=90, progress=lambda t: log(t), plain=True)
            if hasattr(self.s, "status"):
                self.s.status("Game window chosen. Connecting the video...")
            self.keepalive.append(fd)
            chain += [self._mk("pipewiresrc", {"fd": fd, "path": str(node), "do-timestamp": True}),
                      self._mk("videorate", {"drop-only": True}), self._caps(f"video/x-raw,framerate=0/1,max-framerate={o['fps']}/1")]
        elif o["capture"] == "test":      # a moving test picture (automatic tests on machines with no game)
            chain += [self._mk("videotestsrc", {"is-live": True, "pattern": "ball"}),
                      self._caps(f"video/x-raw,width=640,height=480,framerate={o['fps']}/1")]
        else:
            props = {"use-damage": False, "show-pointer": False}
            if o.get("window"):
                props["xid"] = int(o["window"], 0)
            chain += [self._mk("ximagesrc", props), self._caps(f"video/x-raw,framerate={o['fps']}/1")]
        enc_name, enc_props = o["encoder"]
        chain += [self._mk("videoconvert"),
                  self._mk("queue", {"max-size-buffers": 2, "max-size-bytes": 0, "max-size-time": 0, "leaky": "downstream"}),
                  self._mk(enc_name, enc_props),
                  self._caps(f"video/x-h264,profile={o['profile']}"),
                  self._mk("h264parse", {"config-interval": -1}),
                  self._mk("rtph264pay", {"config-interval": -1, "pt": 96, "aggregate-mode": "zero-latency"}),
                  self._caps("application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000")]
        self._link(chain)
        if self.timer:
            self._attach_probes(chain, enc_name)
        log(f"video: {o['capture']} -> {enc_name} @ {o['kbps']} kbps, {o['fps']} fps")

        # ---- audio (optional): what the game plays, from the default output's monitor
        if o.get("audio"):
            try:
                a = [self._mk("pulsesrc", {"device": o["audio"], "buffer-time": 20000, "latency-time": 10000}),
                     self._mk("audioconvert"), self._mk("audioresample"),
                     self._caps("audio/x-raw,rate=48000,channels=2"),
                     self._mk("opusenc", {"audio-type": "restricted-lowdelay", "frame-size": "10", "bitrate": 128000}),
                     self._mk("rtpopuspay", {"pt": 97}),
                     self._caps("application/x-rtp,media=audio,encoding-name=OPUS,payload=97,clock-rate=48000")]
                self._link(a)
                log("audio:", o["audio"])
            except RuntimeError as e:
                log("no audio:", e)

        self.wb.connect("on-negotiation-needed", self._on_negotiation)
        self.wb.connect("on-ice-candidate", lambda el, idx, cand: self.s.on_ice(idx, cand))
        self.wb.connect("notify::connection-state",
                        lambda el, _p: self.s.on_state(el.get_property("connection-state").value_nick))

        # ---- data channels (made before the offer so they are part of it). webrtcbin only makes them once the
        # pipeline is at least READY; before that it returns nothing (newer GStreamer), so switch it on first.
        self.pipe.set_state(Gst.State.READY)
        unreliable = Gst.Structure.new_empty("opts")
        unreliable.set_value("ordered", False)
        unreliable.set_value("max-retransmits", 0)
        self.pad_ch = self.wb.emit("create-data-channel", "pad", unreliable)
        self.ctl_ch = self.wb.emit("create-data-channel", "ctl", None)
        if self.pad_ch is None or self.ctl_ch is None:
            raise RuntimeError("GStreamer's WebRTC part wouldn't open the controller channel "
                               f"(GStreamer {Gst.version_string()}). Please report this.")
        self.pad_ch.connect("on-message-data", lambda ch, b: self.s.on_pad(b.get_data() if b else b""))
        self.ctl_ch.connect("on-message-string", self._on_ctl_string)
        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", lambda b, m: (log("GStreamer error:", m.parse_error()[0].message, m.parse_error()[1]),
                                                    hasattr(self.s, "status") and self.s.status(
                                                        "The stream stopped on your friend's computer: " + m.parse_error()[0].message),
                                                    self.s.end("pipeline error: " + m.parse_error()[0].message)))
        self.pipe.set_state(Gst.State.PLAYING)
        self._set_sendonly()
        self.playing = True
        from gi.repository import GLib
        GLib.timeout_add(200, self._make_offer)

    def _attach_probes(self, chain, enc_name):
        """Time every frame through capture -> convert -> encode -> packetise. Failures here never stop the stream."""
        try:
            from gi.repository import Gst, GLib
            name = lambda e: e.get_factory().get_name()
            enc = next(e for e in chain if name(e) == enc_name)
            pay = next(e for e in chain if name(e) == "rtph264pay")
            def probe(el, padname, stage):
                def cb(pad, info):
                    buf = info.get_buffer()
                    if buf is not None and buf.pts != Gst.CLOCK_TIME_NONE:
                        age = None
                        if stage == "cap":
                            clock = self.pipe.get_clock()
                            if clock is not None:
                                age = (clock.get_time() - self.pipe.get_base_time() - buf.pts) / 1e6
                        self.timer.mark(stage, buf.pts, time.monotonic(), age)
                    return Gst.PadProbeReturn.OK
                el.get_static_pad(padname).add_probe(Gst.PadProbeType.BUFFER, cb)
            probe(chain[0], "src", "cap")
            probe(enc, "sink", "enc_in")
            probe(enc, "src", "enc_out")
            probe(pay, "src", "pay")
            GLib.timeout_add_seconds(2, self._report_timing)
        except Exception as e:
            log("note: host timing is off:", e)
            self.timer = None

    def _report_timing(self):
        if not self.timer or not self.pipe:
            return False
        summ = self.timer.summary()
        if summ:
            self.s.host_stats = summ
            self.send_ctl({"t": "host", "s": summ})
        return True

    def _link(self, els):
        for e in els:
            self.pipe.add(e)
        for a, b in zip(els, els[1:]):
            if not a.link(b):
                raise RuntimeError(f"could not connect {a.get_name()} to {b.get_name()} (format mismatch)")
        if not els[-1].link(self.wb):
            raise RuntimeError("could not connect the stream to webrtcbin")

    def _set_sendonly(self):
        try:
            from gi.repository import GstWebRTC
            i = 0
            while True:     # get-transceiver one by one (the list form isn't iterable in newer PyGObject)
                t = self.wb.emit("get-transceiver", i)
                if t is None:
                    break
                t.set_property("direction", GstWebRTC.WebRTCRTPTransceiverDirection.SENDONLY)
                i += 1
        except Exception as e:
            log("note: couldn't set send-only:", e)

    # ---- negotiation
    def _on_negotiation(self, el):
        # webrtcbin can ask this before the pipeline runs and the video format is known; an offer made then is
        # empty. The offer is made once the pipeline is PLAYING (_make_offer, started from start()).
        if self.playing:
            self._make_offer()

    def _make_offer(self, attempt=0):
        from gi.repository import GLib, Gst
        if self.offered or self.wb is None:
            return False

        def created(promise, _el, _u):
            reply = promise.get_reply()
            offer = reply.get_value("offer") if reply else None
            if offer is None or offer.sdp is None:
                err = reply.get_value("error") if reply and reply.has_field("error") else None
                log(f"offer not ready yet (try {attempt + 1}):", err.message if err else "no details")
                if attempt < 20:
                    GLib.timeout_add(300, lambda: self._make_offer(attempt + 1))
                else:
                    GLib.idle_add(lambda: (self.s.end("couldn't start: the computer couldn't set up the video "
                                                      "connection (WebRTC offer)"), False)[1])
                return
            if self.offered:
                return
            self.offered = True
            self.wb.emit("set-local-description", offer, Gst.Promise.new())
            self.s.on_offer(offer.sdp.as_text())
        self.wb.emit("create-offer", None, Gst.Promise.new_with_change_func(created, None, None))
        return False

    def set_answer(self, sdp):
        from gi.repository import Gst, GstSdp, GstWebRTC
        ok, msg = GstSdp.SDPMessage.new()
        GstSdp.sdp_message_parse_buffer(bytes(sdp.encode()), msg)
        answer = GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, msg)
        self.wb.emit("set-remote-description", answer, Gst.Promise.new())

    def add_ice(self, index, candidate):
        self.wb.emit("add-ice-candidate", index, candidate)

    # ---- control channel
    def _on_ctl_string(self, ch, text):
        try:
            self.s.on_ctl(json.loads(text))
        except ValueError:
            pass

    def send_ctl(self, obj):
        if self.ctl_ch is None:
            return
        try:      # only once the channel is open (GStreamer complains and drops it before that)
            from gi.repository import GstWebRTC
            if self.ctl_ch.get_property("ready-state") != GstWebRTC.WebRTCDataChannelState.OPEN:
                return
        except Exception:
            pass
        self.ctl_ch.emit("send-string", json.dumps(obj))

    def stop(self):
        from gi.repository import Gst
        if self.pipe:
            self.pipe.set_state(Gst.State.NULL)
            self.pipe = None
        for fd in self.keepalive:
            try:
                os.close(fd)
            except OSError:
                pass
        self.keepalive = []


# ===================================================================== doctor
NEEDED_PLUGINS = [("webrtcbin", "gstreamer1.0-plugins-bad"), ("nicesrc", "gstreamer1.0-nice"),
                  ("rtph264pay", "gstreamer1.0-plugins-good"), ("h264parse", "gstreamer1.0-plugins-bad"),
                  ("videoconvert", "gstreamer1.0-plugins-base"), ("opusenc", "gstreamer1.0-plugins-base"),
                  ("rtpopuspay", "gstreamer1.0-plugins-good")]


ARCH_NAMES = {"gstreamer1.0-plugins-base": "gst-plugins-base", "gstreamer1.0-plugins-good": "gst-plugins-good",
              "gstreamer1.0-plugins-bad": "gst-plugins-bad", "gstreamer1.0-plugins-ugly": "gst-plugins-ugly",
              "gstreamer1.0-nice": "libnice", "gstreamer1.0-pipewire": "gst-plugin-pipewire", "gstreamer1.0-x": "gst-plugins-base",
              "gstreamer1.0-vaapi": "gstreamer-vaapi", "gstreamer1.0-libav": "gst-libav"}


def is_arch_like():
    try:
        t = open("/etc/os-release").read().lower()
    except OSError:
        return False
    return any(n in t for n in ("arch", "cachyos", "manjaro", "endeavouros", "steamos"))


def doctor_lines(elements, session_type, uinput_msg, have_pactl, display=True, arch=None):
    """Pure function: describe what's ready and what isn't. elements = set of GStreamer element names or None."""
    arch = is_arch_like() if arch is None else arch
    lines = _doctor_lines(elements, session_type, uinput_msg, have_pactl, display)
    if not arch:
        return lines
    out = []
    for l in lines:
        for deb, a in ARCH_NAMES.items():
            l = l.replace(deb, a)
        out.append(l)
    missing = sorted({a for l in out if "(package: " in l for a in [l.split("(package: ")[1].rstrip(")")]})
    if missing:
        out.append("    Install the missing parts: sudo pacman -S --needed " + " ".join(missing))
    return out


def _doctor_lines(elements, session_type, uinput_msg, have_pactl, display=True):
    out = []
    if elements is None:
        out.append("[X] GStreamer for Python is not installed.")
        out.append("    Debian/Ubuntu: sudo apt install python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-bad-1.0 "
                   "gstreamer1.0-plugins-{base,good,bad,ugly} gstreamer1.0-nice gstreamer1.0-vaapi")
        out.append("    Fedora: sudo dnf install python3-gobject gstreamer1-plugins-{base,good,bad-free,ugly} "
                   "gstreamer1-plugin-libav libnice-gstreamer1")
        out.append("    Arch / CachyOS / Manjaro: sudo pacman -S --needed python-gobject gst-plugins-base gst-plugins-good "
                   "gst-plugins-bad gst-plugins-ugly gst-libav gst-plugin-pipewire libnice gstreamer-vaapi")
        return out
    for el, pkg in NEEDED_PLUGINS:
        out.append(f"[{'OK' if el in elements else 'X '}] {el}" + ("" if el in elements else f"   (package: {pkg})"))
    name, _ = pick_encoder(elements)
    if name:
        hw = name != "x264enc"
        out.append(f"[OK] video encoder: {name}" + ("  (hardware, lowest latency)" if hw else "  (software; works, but uses CPU and adds a few ms)"))
        if not hw:
            out.append("     For lower latency install a hardware encoder: NVIDIA -> gstreamer nvcodec (plugins-bad), "
                       "Intel/AMD -> gstreamer1.0-vaapi or gst-plugin-va")
    else:
        out.append("[X ] no H.264 encoder found. Install gstreamer1.0-plugins-ugly (x264enc) or gstreamer1.0-vaapi.")
    if session_type == "wayland":
        ok = "pipewiresrc" in elements
        out.append(f"[{'OK' if ok else 'X '}] Wayland capture (pipewiresrc + the desktop's screen-share picker)"
                   + ("" if ok else "   (package: gstreamer1.0-pipewire)"))
    else:
        out.append(f"[{'OK' if 'ximagesrc' in elements else 'X '}] X11 capture (ximagesrc)"
                   + ("" if 'ximagesrc' in elements else "   (package: gstreamer1.0-x)"))
    out.append(f"[{'OK' if 'pulsesrc' in elements and have_pactl else '! '}] game audio capture (pulsesrc + pactl)"
               + ("" if 'pulsesrc' in elements and have_pactl else "   (video will still work, without sound)"))
    out.append("[OK] virtual controller (/dev/uinput)" if not uinput_msg else "[X ] virtual controller: " + uinput_msg)
    return out


def encoder_works(name, props, profile, frames=30, timeout=8):
    """-> (ok, problem). Encode a few test frames with this encoder before streaming with it.

    A hardware encoder can be installed yet fail on this GPU or driver (or refuse the browser-safe profile);
    finding that out here lets the stream fall back to software instead of stopping on the friend's screen."""
    from gi.repository import Gst
    pipe = Gst.Pipeline.new("encoder-check")
    try:
        els = []
        for factory, p in (("videotestsrc", {"num-buffers": frames}), ("capsfilter", None), ("videoconvert", None),
                           (name, props), ("capsfilter", None), ("h264parse", None), ("fakesink", {"sync": False})):
            el = Gst.ElementFactory.make(factory, None)
            if el is None:
                return False, f"GStreamer element '{factory}' is missing"
            for k, v in (p or {}).items():
                GstBackend._set(el, k, v)
            els.append(el)
            pipe.add(el)
        els[1].set_property("caps", Gst.Caps.from_string("video/x-raw,width=1280,height=720,framerate=60/1"))
        els[4].set_property("caps", Gst.Caps.from_string(f"video/x-h264,profile={profile}"))
        for a, b in zip(els, els[1:]):
            if not a.link(b):
                return False, f"{a.get_name()} won't connect to {b.get_name()}"
        if pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            return False, f"{name} wouldn't start"
        msg = pipe.get_bus().timed_pop_filtered(timeout * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if msg is None:
            return False, f"{name} didn't finish {frames} test frames in {timeout} s"
        if msg.type == Gst.MessageType.ERROR:
            return False, msg.parse_error()[0].message
        return True, ""
    finally:
        pipe.set_state(Gst.State.NULL)


def available_elements():
    try:
        import gi
        gi.require_version("Gst", "1.0")
        gi.require_version("GstWebRTC", "1.0")
        gi.require_version("GstSdp", "1.0")
        from gi.repository import Gst
        Gst.init(None)
    except Exception:
        return None
    names = {"webrtcbin", "nicesrc", "rtph264pay", "h264parse", "videoconvert", "opusenc", "rtpopuspay", "pulsesrc",
             "ximagesrc", "pipewiresrc", "nvh264enc", "vah264lpenc", "vah264enc", "vaapih264enc", "x264enc"}
    return {n for n in names if Gst.ElementFactory.find(n)}


def default_monitor():
    """Name of the default output's monitor source (what the game is playing), or ''."""
    import subprocess
    try:
        sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=3).stdout.strip()
        return sink + ".monitor" if sink else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# ===================================================================== main
def portal_log():
    """The desktop's own screen-sharing messages from the last few minutes (user journal, nothing leaves the PC)."""
    try:
        r = subprocess.run(["journalctl", "--user", "-b", "--since", "-10 min", "--no-pager", "-o", "cat",
                            "-u", "xdg-desktop-portal.service", "-u", "plasma-xdg-desktop-portal-kde.service",
                            "-u", "xdg-desktop-portal-kde.service"], capture_output=True, text=True, timeout=10)
        return [l for l in r.stdout.splitlines() if l.strip()][-15:]
    except (OSError, subprocess.SubprocessError):
        return []


def restart_portal():
    """Restart this user's screen-sharing service (clears a box stuck from an earlier try). No admin rights needed."""
    for unit in ("plasma-xdg-desktop-portal-kde.service", "xdg-desktop-portal-kde.service", "xdg-desktop-portal.service"):
        try:
            subprocess.run(["systemctl", "--user", "restart", unit], capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass
    time.sleep(2)


def test_share(session_type):
    if session_type != "wayland":
        print("OK: this desktop doesn't need the 'share your screen' box (X11).")
        return 0
    import portal_capture
    from gi.repository import GLib

    def attempt(**kw):
        out, loop = {}, GLib.MainLoop()
        def go():
            try:
                out["r"] = portal_capture.request_screencast(progress=lambda t: print(t, flush=True), **kw)
            except Exception as e:
                out["e"] = str(e)
            loop.quit()
            return False
        GLib.idle_add(go)
        loop.run()
        return out

    out = attempt(timeout=40)
    if "e" in out and "in time" in out["e"]:
        print("No box after 40 s. What the desktop logged:", flush=True)
        for l in portal_log() or ["(nothing)"]:
            print("  | " + l, flush=True)
        print("Restarting the desktop's screen-sharing service (clears a box stuck from an earlier try) "
              "and asking again the simplest way...", flush=True)
        restart_portal()
        out = attempt(timeout=60, plain=True)
    if "r" in out:
        print("OK: screen sharing works. Next time it uses the same window without asking.")
        return 0
    print("Problem: " + out["e"])
    for l in portal_log():
        print("  | " + l)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Netplay Matchmaker stream host")
    ap.add_argument("--mm", help="matchmaker address for the stream connection, e.g. 1.2.3.4:7777")
    ap.add_argument("--server", help="DEPRECATED: matchmaker web address (older matchmakers), e.g. http://1.2.3.4:7778")
    ap.add_argument("--key", help="session key from the match")
    ap.add_argument("--doctor", action="store_true", help="check this computer and exit")
    ap.add_argument("--test-share", action="store_true", help="open the 'share your screen' box once and say if it worked")
    ap.add_argument("--capture", choices=["auto", "x11", "portal", "test"], default="auto")
    ap.add_argument("--window", help="X11 window id to capture (default: whole screen)")
    ap.add_argument("--encoder", default="auto", help="auto, nvh264enc, vah264lpenc, vah264enc, vaapih264enc, x264enc")
    ap.add_argument("--kbps", type=int, default=8000)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--profile", default="constrained-baseline", help="H.264 profile (constrained-baseline is safest for browsers)")
    ap.add_argument("--slot", type=int, default=2, help="which player the guest controls")
    ap.add_argument("--slot-pick", action="store_true", help="let the guest choose Player 2, 3 or 4 (a friend's privilege)")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-timing", action="store_true", help="skip measuring the host's capture/encode time")
    a = ap.parse_args(argv)

    elements = available_elements()
    session_type = os.environ.get("XDG_SESSION_TYPE", "x11")
    if a.doctor:
        lines = doctor_lines(elements, session_type, uinput_pad.uinput_problem(), bool(shutil.which("pactl")))
        print("\n".join(lines))
        return 0 if not any(l.startswith("[X") for l in lines) else 1
    if a.test_share:
        return test_share(session_type)
    if not (a.mm or a.server) or not a.key:
        ap.error("--mm (or --server) and --key are required (or use --doctor)")
    if elements is None:
        print("\n".join(doctor_lines(None, session_type, "", False)))
        return 2
    enc = pick_encoder(elements, a.encoder, a.kbps, a.fps)
    if enc[0] is None:
        print("No usable H.264 encoder. Run --doctor.")
        return 2
    if a.encoder == "auto" and enc[0] != "x264enc":
        works, why = encoder_works(enc[0], enc[1], a.profile)
        if not works:
            log(f"encoder {enc[0]} failed a quick test ({why})")
            if "x264enc" not in elements:
                print(f"STREAM ERROR: the video encoder {enc[0]} doesn't work here ({why}). "
                      "Install gst-plugins-ugly for the software encoder.", flush=True)
                return 2
            log("using the software encoder x264enc instead")
            enc = pick_encoder(elements, "x264enc", a.kbps, a.fps)
    problem = uinput_pad.uinput_problem()
    if problem:
        print("Can't create the virtual controller:", problem)
        return 2

    from gi.repository import GLib
    import signal as _signal
    def stopped(*_a):
        try:
            import portal_capture
            portal_capture.withdraw_pending()       # never leave a hidden 'share your screen' box behind
        except Exception:
            pass
        os._exit(0)
    _signal.signal(_signal.SIGTERM, stopped)
    capture = a.capture if a.capture != "auto" else ("portal" if session_type == "wayland" else "x11")
    audio = "" if a.no_audio else default_monitor()
    opts = {"capture": capture, "window": a.window, "encoder": enc, "kbps": a.kbps, "fps": a.fps,
            "profile": a.profile, "audio": audio, "timing": not a.no_timing}
    loop = GLib.MainLoop()
    pad = uinput_pad.UinputPad(f"Netplay Virtual Pad (Player {a.slot})")
    pad.open()
    on_msg = lambda m: GLib.idle_add(lambda: session.on_signal(m) and False)
    on_end = lambda why: GLib.idle_add(lambda: (session.end(why), False)[1])
    if a.mm:
        import stream_signal
        host, port = stream_signal.parse_address(a.mm)
        sig = stream_signal.TcpSignaling(host, port, a.key, on_msg=on_msg, on_end=on_end)
        try:
            ice = sig.connect().get("iceServers", [])
        except stream_signal.SignalError as e:
            print(f"STREAM ERROR: can't reach the matchmaker: {e}", flush=True)
            pad.close()
            return 2
    else:
        sig = Signaling(a.server, a.key, on_msg=on_msg, on_end=on_end)
        try:
            ice = sig.get_json("/cfg").get("iceServers", [])
        except (urllib.error.URLError, OSError, ValueError) as e:
            print("Can't reach the matchmaker:", e)
            pad.close()
            return 2
    def new_pad(n):
        p = uinput_pad.UinputPad(f"Netplay Virtual Pad (Player {n})")
        p.open()
        return p
    session = Session(sig, lambda s: GstBackend(s, opts, ice), pad, slot=a.slot,
                      pick=[2, 3, 4] if a.slot_pick else None, pad_factory=new_pad)
    def tick():
        session.watchdog_tick()
        if session.ended.is_set():
            loop.quit()
            return False
        return True
    GLib.timeout_add(100, tick)
    sig.start()
    log("waiting for the guest to connect...")
    try:
        loop.run()
    except KeyboardInterrupt:
        session.end("interrupted")
    finally:
        if hasattr(sig, "flush"):
            sig.flush()
        time.sleep(0.3)      # let the final 'bye' go out
        sig.stop()
        session.pad.close()   # (the guest may have switched to another player's pad)
    why = plain_reason(getattr(session, "why", ""))
    if why:
        print(f"STREAM ERROR: {why}", flush=True)   # the app shows this line when the stream stops
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
