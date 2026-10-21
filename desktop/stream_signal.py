"""The stream connection to the matchmaker (TCP, one JSON object per line; see PROTOCOL.md, Built-in streaming).

After a match on the built-in stream, the host and the guest each open one with their own session key and swap their
WebRTC setup messages (hello / offer / answer / ice / status / bye) through it. The matchmaker also hands out the ICE
servers (STUN, and a short-lived relay password if it has a relay). Plain Python with threads, so the stream host,
the player and the app can all use it.
"""
import json
import queue
import socket
import threading
import time

import mmtls

LINE_LIMIT = 32768
PING_SECONDS = 20


class SignalError(Exception):
    """The matchmaker said no (why = 'bad_key', 'expired', ...) or couldn't be reached (why = 'unreachable')."""

    def __init__(self, msg, why=""):
        super().__init__(msg)
        self.why = why


def parse_address(text, default_port=7777):
    """'host:port', '[v6]:port' or 'host' -> (host, port)."""
    text = str(text).strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = rest.lstrip(":")
    elif text.count(":") == 1:
        host, port = text.split(":")
    else:
        host, port = text, ""
    return host, int(port) if port.isdigit() else default_port


class TcpSignaling:
    """on_msg(dict) for each message from the other side; on_end(why) once, when the session is over."""

    def __init__(self, host, port, key, on_msg=None, on_end=None, timeout=8, reconnect_for=20):
        self.addr, self.key, self.timeout, self.reconnect_for = (host, int(port)), key, timeout, reconnect_for
        self.on_msg, self.on_end = on_msg or (lambda m: None), on_end or (lambda why: None)
        self.out, self.stop_flag, self.lock = queue.Queue(), threading.Event(), threading.Lock()
        self.sock = self.file = None
        self.config = {}
        self._ended = False

    # ---- connecting
    def connect(self):
        """Open the connection and return the matchmaker's answer ({"role", "game", "iceServers", "relay"})."""
        try:
            sock = mmtls.connect(self.addr[0], self.addr[1], self.timeout)     # (encrypted when the matchmaker offers it)
        except mmtls.TlsError as e:
            raise SignalError(str(e), "tls")
        except OSError as e:
            raise SignalError(f"Couldn't reach the matchmaker at {self.addr[0]}:{self.addr[1]} ({e}).", "unreachable")
        try:
            sock.settimeout(self.timeout)
            sock.sendall((json.dumps({"v": 1, "op": "stream", "key": self.key}) + "\n").encode())
            f = sock.makefile("rb")
            line = f.readline(LINE_LIMIT)
            reply = json.loads(line) if line else None
        except (OSError, ValueError) as e:
            sock.close()
            raise SignalError(f"The matchmaker didn't answer ({e or 'connection closed'}).", "unreachable")
        if not isinstance(reply, dict) or reply.get("op") != "stream":
            sock.close()
            msg = reply.get("msg") if isinstance(reply, dict) else ""
            why = reply.get("why", "") if isinstance(reply, dict) else ""
            if isinstance(reply, dict) and reply.get("op") == "error":
                raise SignalError(str(msg or "The matchmaker refused the stream."), why)
            raise SignalError("This matchmaker doesn't support the stream connection (it may be an older version).", "old")
        sock.settimeout(PING_SECONDS * 3.5)
        with self.lock:
            self.sock, self.file = sock, f
        self.config = reply
        return reply

    def start(self):
        self.reader = threading.Thread(target=self._read_loop, daemon=True)
        self.reader.start()
        threading.Thread(target=self._send_loop, daemon=True).start()
        threading.Thread(target=self._ping_loop, daemon=True).start()

    # ---- sending
    def send(self, msg):
        self.out.put({"op": "sig", "msg": msg})

    def stop(self):
        self.stop_flag.set()
        self.out.put(None)
        with self.lock:
            s = self.sock
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)      # wakes the reading thread, which then closes it: closing it under that
            except OSError:                       # thread could hand its number to a new connection, and the old read
                pass                              # would eat that connection's first bytes
            reader = getattr(self, "reader", None)
            if not (reader and reader.is_alive()) or reader is threading.current_thread():
                s.close()

    def flush(self, timeout=2.0):
        """Wait (a little) until everything queued has really been written (the goodbye must not be lost)."""
        end = time.monotonic() + timeout
        while self.out.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.02)

    def _write(self, obj):
        with self.lock:
            s = self.sock
        if s is None:
            raise OSError("not connected")
        s.sendall((json.dumps(obj) + "\n").encode())

    def _send_loop(self):
        pending = None
        while not self.stop_flag.is_set():
            item = pending or self.out.get()
            if item is None:
                return
            try:
                self._write(item)
                pending = None
                self.out.task_done()
            except OSError:
                pending = item          # sent again once the connection is back
                time.sleep(0.2)

    def _ping_loop(self):
        while not self.stop_flag.wait(PING_SECONDS):
            self.out.put({"op": "ping"})

    # ---- receiving
    def _end(self, why):
        if not self._ended:
            self._ended = True
            self.stop()
            self.on_end(why)

    def _read_loop(self):
        try:
            self._read_lines()
        finally:
            if self.stop_flag.is_set():
                with self.lock:
                    s = self.sock
                if s:
                    s.close()

    def _read_lines(self):
        while not self.stop_flag.is_set():
            with self.lock:
                f = self.file
            try:
                line = f.readline(LINE_LIMIT)
            except (OSError, ValueError):
                line = b""
            if self.stop_flag.is_set():
                return
            if not line:
                if self.config.get("_bye"):
                    return self._end("session closed")
                if not self._reconnect():
                    return
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if not isinstance(m, dict):
                continue
            if m.get("op") == "sig" and isinstance(m.get("msg"), dict):
                if m["msg"].get("type") == "bye":
                    self.config["_bye"] = True
                self.on_msg(m["msg"])
            elif m.get("op") == "error":
                return self._end(str(m.get("msg") or "the matchmaker ended the stream"))

    def _reconnect(self):
        """The connection dropped (Wi-Fi hiccup, matchmaker restart): try again for a while with the same key."""
        end = time.monotonic() + self.reconnect_for
        delay = 0.5
        while not self.stop_flag.is_set() and time.monotonic() < end:
            try:
                self.connect()
                return True
            except SignalError as e:
                if e.why in ("bad_key", "expired"):
                    self._end("session ended")
                    return False
            time.sleep(delay)
            delay = min(delay * 2, 4)
        self._end("lost contact with the matchmaker")
        return False


def without_relay(ice):
    """The ICE servers minus any relay (the player chose not to send their stream through one)."""
    out = []
    for s in ice or []:
        urls = [s.get("urls")] if isinstance(s.get("urls"), str) else list(s.get("urls") or [])
        keep = [u for u in urls if not str(u).startswith(("turn:", "turns:"))]
        if keep:
            out.append({"urls": keep})
    return out


def send_once(host, port, key, msgs, timeout=4):
    """Open a stream connection, send these messages, close. For short notes (the app telling the friend why the
    stream stopped). Returns True if sent."""
    sig = TcpSignaling(host, port, key, timeout=timeout)
    try:
        sig.connect()
        for m in msgs:
            sig._write({"op": "sig", "msg": m})
        time.sleep(0.1)
        return True
    except (SignalError, OSError):
        return False
    finally:
        sig.stop()
