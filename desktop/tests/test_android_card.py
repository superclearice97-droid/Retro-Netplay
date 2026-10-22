"""The phone app's title card (GameActivity) in an Android emulator: made-up box art and screenshots (no real game
art), then a gameplay clip made here with GStreamer. Checks the card is built, shows the box art, plays the slideshow,
then plays the clip, the background moves, and Play is offered. Screenshots go to ./android-card/.
Needs: adb with one emulator running, the app and its test APK installed (CI: the android-player job), GStreamer x264enc."""
import json, os, re, shutil, subprocess, sys, tempfile, threading, time

try:
    assert shutil.which("adb") and "\tdevice" in subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
    assert shutil.which("gst-launch-1.0")
except AssertionError:
    print("SKIP (needs an Android emulator on adb and gst-launch-1.0)")
    sys.exit(0)

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(extra)[-3000:]}]" if extra and not cond else ""), flush=True)

OUT = os.path.abspath("android-card")
os.makedirs(OUT, exist_ok=True)
APP = "net.netplay.matchmaker"
FILES = f"/data/data/{APP}/files"         # the app's private folder (the debug app lets adb in with run-as)

def card(video="", seconds=7, shot=None):
    subprocess.run(["adb", "logcat", "-c"], capture_output=True)
    if shot:
        def take():
            time.sleep(seconds - 2)
            with open(os.path.join(OUT, shot), "wb") as f:
                f.write(subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True).stdout)
        threading.Thread(target=take, daemon=True).start()
    extra = ["-e", "video", video] if video else []        # (adb drops an empty argument, which breaks the command)
    r = subprocess.run(["adb", "shell", "am", "instrument", "-w", "-e", "mode", "card"] + extra + ["-e", "seconds", str(seconds),
                        "net.netplay.matchmaker.test/net.netplay.app.PlayerCheck"], capture_output=True, text=True, timeout=200)
    m = re.search(r"result=(\{.*\})", r.stdout)
    log = subprocess.run(["adb", "logcat", "-d", "-s", "AndroidRuntime:E", "MediaPlayer:E", "VideoView:E"], capture_output=True, text=True).stdout
    return (json.loads(m.group(1)) if m else {}), r.stdout + "\n--- logcat:\n" + log[-4000:]

# 1) box art and screenshots (this also makes the app's own files folder)
res, raw = card(shot="card-slides.png")
print("card:", json.dumps(res), flush=True)
check("the card is built", res.get("built") is True, raw)
check("the box art is shown", res.get("box") is True, res)
check("the gameplay window has the two screenshots", res.get("slides") == 2, res)
check("the background moves (frames drawn)", res.get("frames", 0) >= 20, res)
check("Play is offered", res.get("button") == "PLAY", res)

# 2) a gameplay clip (a GStreamer test picture, 3 seconds of H.264 in MP4)
clip = os.path.join(tempfile.mkdtemp(), "clip.mp4")
subprocess.run(["gst-launch-1.0", "-q", "videotestsrc", "num-buffers=90", "pattern=ball", "!", "video/x-raw,width=480,height=320,framerate=30/1",
                "!", "x264enc", "!", "video/x-h264,profile=baseline", "!", "mp4mux", "!", "filesink", f"location={clip}"], check=True)
push = subprocess.run(["adb", "push", clip, "/data/local/tmp/clip.mp4"], capture_output=True, text=True)
cp = subprocess.run(["adb", "shell", f"run-as {APP} sh -c 'mkdir -p files && cp /data/local/tmp/clip.mp4 files/clip.mp4 && ls -l files/clip.mp4'"],
                    capture_output=True, text=True)
if "clip.mp4" not in cp.stdout:      # some versions can't read /data/local/tmp as the app: stream it in instead
    with open(clip, "rb") as f:
        cp = subprocess.run(["adb", "exec-in", f"run-as {APP} sh -c 'mkdir -p files && cat > files/clip.mp4'"], stdin=f, capture_output=True)
    cp = subprocess.run(["adb", "shell", f"run-as {APP} ls -l files/clip.mp4"], capture_output=True, text=True)
check("the clip reaches the app's own files folder", "clip.mp4" in cp.stdout, push.stdout + push.stderr + cp.stdout + cp.stderr)
res, raw = card(video=FILES + "/clip.mp4", seconds=9, shot="card-video.png")
print("card with a clip:", json.dumps(res), flush=True)
check("the gameplay clip plays (looping, muted)", res.get("video") is True, raw)
check("the card with a clip still shows the box art", res.get("box") is True, res)

# 3) the ScreenScraper download on the phone, from a pretend ScreenScraper: saved in the app's own folder, given to
#    the game the way the app does it, and shown on its card
r = subprocess.run(["adb", "shell", "am", "instrument", "-w", "-e", "mode", "art", "net.netplay.matchmaker.test/net.netplay.app.PlayerCheck"],
                   capture_output=True, text=True, timeout=200)
m = re.search(r"result=(\{.*\})", r.stdout)
res = json.loads(m.group(1)) if m else {}
print("download:", json.dumps(res), flush=True)
art = res.get("art") or {}
check("download: box art and screenshot saved", sorted(res.get("saved") or []) == ["box", "screen"], r.stdout[-2000:])
check("download: saved in the app's own private folder", f"/{APP}/files/art/gba/media/box2dfront/" in str(art.get("box", "")), art)
check("download: the details reach the game", art.get("name") == "Downloaded Test Game" and art.get("year") == "2001"
      and art.get("desc") == "Made up for the test.", art)
check("download: the card shows the downloaded box art and screenshot", (res.get("card") or {}).get("box") is True
      and (res.get("card") or {}).get("slides") == 1, res.get("card"))

print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
