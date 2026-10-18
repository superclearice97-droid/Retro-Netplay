"""The 'share your screen' (desktop portal) handshake, against a stand-in portal on a private D-Bus.
Needs dbus-run-session and a Python with PyGObject; skips otherwise."""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY = next((p for p in (sys.executable, "/usr/bin/python3.12", "/usr/bin/python3")
           if p and subprocess.run([p, "-c", "from gi.repository import Gio"], capture_output=True).returncode == 0), None)
if not PY or not shutil.which("dbus-run-session"):
    print("SKIP (no PyGObject or dbus-run-session)")
    sys.exit(0)

home = tempfile.mkdtemp()
host = os.path.join(HERE, "..", "stream_host.py")
script = f"""
{PY} {HERE}/mock_portal.py & P=$!
sleep 1
XDG_SESSION_TYPE=wayland HOME={home} {PY} {host} --test-share; echo "exit=$?"
XDG_SESSION_TYPE=wayland HOME={home} {PY} {host} --test-share; echo "exit=$?"
kill $P; sleep 0.3
XDG_SESSION_TYPE=wayland HOME={home} timeout 20 {PY} {host} --test-share; echo "exit=$?"
"""
log = os.path.join(home, "mock.log")
r = subprocess.run(["dbus-run-session", "--", "sh", "-c", script], capture_output=True, text=True, timeout=90,
                   env={**os.environ, "MOCK_LOG": log, "PICK_DELAY_MS": "300"})
out = r.stdout
calls = open(log).read() if os.path.exists(log) else ""
fails = 0
def check(name, ok, info=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  [{info}]"))
    fails += 0 if ok else 1
check("sharing works through the portal (create, select, start, open)", out.count("OK: screen sharing works") == 2, out)
check("the choice is remembered (restore token sent the second time)", "restore_token" in calls.splitlines()[5] if len(calls.splitlines()) > 5 else False, calls)
check("no portal: plain-words problem, not a hang", "no screen-sharing service" in out and out.strip().endswith("exit=1"), out)
print("ALL PASSED" if not fails else "SOME FAILED")
sys.exit(1 if fails else 0)
