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
sels = [l for l in calls.splitlines() if "SelectSources" in l]
check("the choice is remembered (restore token sent the second time)", len(sels) >= 2 and "restore_token" in sels[1], calls)
check("the share stays open after the window is chosen (the desktop ends it if the app's connection closes)",
      "its app left the bus" not in calls and calls.count("session closed: the app closed it") == 2, calls)
check("no portal: plain-words problem, not a hang", "no screen-sharing service" in out and out.strip().endswith("exit=1"), out)
# an older desktop (version 3, no hidden pointer) and one that refuses the extra options: still works
for name, env in (("older desktop", {"MOCK_VERSION": "3", "MOCK_CURSORS": "2"}), ("desktop refusing extras", {"MOCK_REFUSE_EXTRAS": "1"})):
    log2 = os.path.join(home, name.replace(" ", "_") + ".log")
    sc = f"""
{PY} {HERE}/mock_portal.py & P=$!
sleep 1
XDG_SESSION_TYPE=wayland HOME={tempfile.mkdtemp()} {PY} {host} --test-share; echo "exit=$?"
kill $P
"""
    r2 = subprocess.run(["dbus-run-session", "--", "sh", "-c", sc], capture_output=True, text=True, timeout=60,
                        env={**os.environ, "MOCK_LOG": log2, "PICK_DELAY_MS": "200", **env})
    sel = [l for l in open(log2).read().splitlines() if "SelectSources" in l]
    check(f"{name}: sharing still works", "OK: screen sharing works" in r2.stdout, r2.stdout)
    if name == "older desktop":
        check("older desktop: no options it doesn't know are sent", sel and "persist_mode" not in sel[-1] and "cursor_mode" not in sel[-1], sel)
    else:
        check("refusing desktop: asked again the plain way", len(sel) == 2 and "persist_mode" not in sel[1], sel)
# a box stuck from an earlier try: the first ask is never answered -> withdraw it, reset, ask again plainly
log3 = os.path.join(home, "stuck.log")
sc = f"""
{PY} {HERE}/mock_portal.py & P=$!
sleep 1
XDG_SESSION_TYPE=wayland HOME={tempfile.mkdtemp()} timeout 80 {PY} {host} --test-share; echo "exit=$?"
kill $P
"""
r3 = subprocess.run(["dbus-run-session", "--", "sh", "-c", sc], capture_output=True, text=True, timeout=120,
                    env={**os.environ, "MOCK_LOG": log3, "PICK_DELAY_MS": "200", "MOCK_STUCK_FILE": os.path.join(home, "stuck")})
calls3 = open(log3).read()
check("stuck box: the unanswered request is withdrawn", "call Close" in calls3, calls3)
check("stuck box: asks again and works", "OK: screen sharing works" in r3.stdout and "Restarting" in r3.stdout, r3.stdout)
check("stuck box: second ask is the simplest one", "persist_mode" not in [l for l in calls3.splitlines() if "SelectSources" in l][-1])
print("--- what the Streaming check window shows (stuck box case) ---")
print(r3.stdout.split("exit=")[0].strip())
print("--- what the Streaming check window shows ---")
print(out.split("exit=")[0].strip())
print("ALL PASSED" if not fails else "SOME FAILED")
sys.exit(1 if fails else 0)
