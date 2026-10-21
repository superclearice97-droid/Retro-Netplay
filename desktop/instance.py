"""One Retro-Netplay window at a time. When the app is started again while it runs (for example by clicking a
netplay:// link), the new start hands what it was given to the running app and quits.

The running app listens on 127.0.0.1 only, on a port it writes to a private file with a random token; a hand-over
must carry that token, so other programs or users on the computer can't push links into it.
"""
import hmac
import json
import os
import secrets
import socket
import threading
from pathlib import Path

PATH = Path.home() / ".netplay-matchmaker" / "instance.json"


def hand_over(args, path=None):
    """True if a running app took these arguments (then this start should quit)."""
    try:
        info = json.loads(Path(path or PATH).read_text())
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=1.5) as s:
            s.sendall((json.dumps({"token": info["token"], "args": list(args)[:4]}) + "\n").encode())
            s.settimeout(2)
            return s.recv(16).startswith(b"ok")
    except (OSError, ValueError, KeyError, TypeError):
        return False


def listen(on_args, path=None):
    """Accept hand-overs from later starts: on_args(list of strings) runs on a background thread."""
    path = Path(path or PATH)
    token = secrets.token_hex(16)
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"port": srv.getsockname()[1], "token": token}, f)
    except OSError:
        srv.close()
        return None

    def serve():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            try:
                c.settimeout(2)
                m = json.loads(c.makefile("rb").readline(8192))
                if isinstance(m, dict) and hmac.compare_digest(str(m.get("token", "")), token):
                    args = [a for a in m.get("args", []) if isinstance(a, str)][:4]
                    c.sendall(b"ok\n")
                    on_args([a[:2000] for a in args])
            except (OSError, ValueError):
                pass
            finally:
                c.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv
