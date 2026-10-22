"""Too much memory: a check that keeps growing (or a program it started) is stopped at the limit and the next way of
starting the player is tried; the app itself writes a report and quits when it passes its limit; normal checks and
time-outs behave as before. Linux only (it reads /proc)."""
import os, subprocess, sys, tempfile, textwrap, time
from pathlib import Path
if not os.path.isdir("/proc"):
    print("SKIP (needs /proc)"); sys.exit(0)
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
home = tempfile.mkdtemp(); os.environ["HOME"] = home
import app, runtime

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{str(info)[-1500:]}]" if info and not ok else ""), flush=True); ok or fails.append(name)

d = Path(home)
# grows by 20 MB every 50 ms up to 2 GB; with "child" it does that in a program it starts
hog = d / "hog.py"
hog.write_text(textwrap.dedent(f"""
    import subprocess, sys, time
    if "child" in sys.argv:
        subprocess.run([sys.executable, {str(hog)!r}])
    print("growing", flush=True)
    keep = []
    for _ in range(100):
        keep.append(bytearray(20 * 1024 * 1024)); time.sleep(0.05)
"""))
fine = d / "fine.py"; fine.write_text("print('[OK] all good')\n")

rc, out, err = runtime.run_capped([sys.executable, str(fine)], 10)
check("a normal check runs as before", rc == 0 and "all good" in out, (rc, out, err))
t = time.monotonic()
rc, out, err = runtime.run_capped([sys.executable, str(hog)], 30, limit_mb=200)
check("a growing check is stopped at the limit", rc is None and time.monotonic() - t < 10, (rc, time.monotonic() - t))
t = time.monotonic()
rc, out, err = runtime.run_capped([sys.executable, str(hog), "child"], 30, limit_mb=200)
time.sleep(0.5)
left = subprocess.run(["pgrep", "-f", str(hog)], capture_output=True, text=True).stdout.strip()
check("memory of programs the check started counts too, and they are stopped with it", rc is None and not left, (rc, left))
try:
    runtime.run_capped([sys.executable, "-c", "import time; time.sleep(5)"], 0.5)
    check("a check that takes too long still times out", False)
except subprocess.TimeoutExpired:
    check("a check that takes too long still times out", True)

# the player check: the bundled player grows, the computer's own one works -> joining works, and the report says why
runtime.CHECK_MEMORY_MB = 200
runtime.guest_commands = lambda: [[sys.executable, str(hog)], [sys.executable, str(fine)]]
ok, report = app.guest_ready(refresh=True)
check("a player check that grows is stopped and the next way is used", ok and app._guest_cache["cmd"][-1] == str(fine), app._guest_cache)
rep = app.MEMORY_REPORT.read_text() if app.MEMORY_REPORT.exists() else ""
check("the report names the check that was stopped", "player window's check used more than 200 MB" in rep and str(hog) in rep, rep)
runtime.guest_commands = lambda: [[sys.executable, str(hog)]]
ok, report = app.guest_ready(refresh=True)
check("with no other way, the setup check says it was stopped", not ok and report.startswith("[X ] The player window's check used"), report)

# the app itself: over its limit it writes where it was and quits
self_home = tempfile.mkdtemp()
r = subprocess.run([sys.executable, "-c", textwrap.dedent(f"""
    import sys, time; sys.path.insert(0, {str(HERE.parent)!r})
    import app
    app.watch_memory(limit_mb=200)
    def busy_part():
        keep = []
        for _ in range(100):
            keep.append(bytearray(20 * 1024 * 1024)); time.sleep(0.05)
        time.sleep(10)
    busy_part()
    print("not stopped")
""")], capture_output=True, text=True, timeout=60, env=dict(os.environ, HOME=self_home))
rep = (Path(self_home) / ".netplay-matchmaker" / "memory-report.txt")
rep = rep.read_text() if rep.exists() else ""
check("the app quits when it passes its limit", r.returncode == 3 and "not stopped" not in r.stdout, (r.returncode, r.stdout, r.stderr))
check("its report says how much and where each part of the app was",
      "MB of memory and stopped itself" in rep and "busy_part" in rep and "Most common Python objects" in rep, rep)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
