"""The real stream host (stream_host.py), with its virtual controller swapped for one that writes every applied
controller state to a file (NETPLAY_PAD_LOG). Automatic tests use it where /dev/uinput isn't available, and to check
that every packet arrived intact. Everything else (capture, encoder, WebRTC, signaling) is the real thing."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import uinput_pad
import stream_host

OUT = os.environ["NETPLAY_PAD_LOG"]


class RecordingPad(uinput_pad.UinputPad):
    def open(self):
        self.f = open(OUT, "a")

    def apply(self, cur):
        if {**cur, "seq": 0} != {**self.state, "seq": 0}:
            self.f.write(json.dumps({**cur, "ax": list(cur["ax"]), "pad": self.name}) + "\n")
            self.f.flush()
        self.state = cur

    def close(self):
        pass


uinput_pad.UinputPad = RecordingPad
uinput_pad.uinput_problem = lambda: ""
sys.exit(stream_host.main())
