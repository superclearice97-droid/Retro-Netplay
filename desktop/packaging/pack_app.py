"""Packages the desktop app for this computer's system: the engine (engine.py, with PyInstaller) with the ready-made
player inside, in the Electron window (desktop/shell), with Electron's codec-free libffmpeg.

  python3 pack_app.py --player <folder that holds Retro-Netplay-Player> [--sdlpy <folder>]

Writes to dist/ (the same names as the release has always used):
  Linux:   Retro-Netplay-x86_64.AppImage, Retro-Netplay-linux-x86_64.zip
  Windows: Retro-Netplay-windows-x86_64.zip
  Mac:     Retro-Netplay-mac-arm64.zip (Apple Silicon; signed ad hoc, not with an Apple developer certificate)
Needs Python 3.10+ with PyInstaller 6.11.1, Node 20+, and internet for npm, electron-builder and Electron's ffmpeg.
"""
import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # desktop/
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "dist")
SHELL = os.path.join(HERE, "shell")
SYS = {"Linux": "linux", "Windows": "win", "Darwin": "mac"}[platform.system()]
EXE = ".exe" if SYS == "win" else ""
NPX = "npx.cmd" if SYS == "win" else "npx"
NPM = "npm.cmd" if SYS == "win" else "npm"
# where Electron keeps libffmpeg in the unpacked app, and the name of Electron's codec-free build of it
FFMPEG = {
    "linux": ("linux-unpacked", "libffmpeg.so", "linux-x64"),
    "win": ("win-unpacked", "ffmpeg.dll", "win32-x64"),
    "mac": ("mac-arm64/Retro-Netplay.app/Contents/Frameworks/Electron Framework.framework/Versions/A/Libraries",
            "libffmpeg.dylib", "darwin-arm64"),
}


def run(cmd, **kw):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def build_engine(work, player, sdlpy):
    notices = os.path.join(work, "notices")
    os.makedirs(notices)
    for f in ("LICENSE", "NOTICE.md", "PRIVACY.md", "TERMS.md", os.path.join("design", "THIRD-PARTY-DESIGN.txt")):
        shutil.copy(os.path.join(ROOT, f), notices)
    data = [("icon-256.png", "."), ("ui", "ui"), ("sounds", "sounds"), ("fonts", "hostpy/fonts")]
    data += [(f, "hostpy") for f in ("stream_host.py", "uinput_pad.py", "portal_capture.py", "stream_signal.py", "mmtls.py",
                                     "stream_guest.py")]
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "Retro-Netplay-Engine",
            "--distpath", os.path.join(work, "dist"), "--workpath", os.path.join(work, "build"), "--specpath", work]
    for src, dst in data:
        args += ["--add-data", os.path.join(HERE, src) + os.pathsep + dst]
    args += ["--add-data", notices + os.pathsep + "notices"]
    if sdlpy:       # SDL for the player script under the computer's own Python (Linux's fallback player)
        for d in ("sdl2", "sdl2dll"):
            args += ["--add-data", os.path.join(sdlpy, d) + os.pathsep + "hostpy/" + d]
    for m in ("server", "lobby", "sessions", "social", "gameinfo"):
        args += ["--hidden-import", m]
    for m in ("stream_host", "stream_guest", "portal_capture", "gi", "tkinter", "pygame", "PIL"):
        args += ["--exclude-module", m]
    # (not --noconsole on Windows: the engine reports its address on standard output, which a windowed build lacks;
    #  the desktop shell starts it with its console window hidden)
    run(args + [os.path.join(HERE, "engine.py")])
    engine = os.path.join(work, "dist", "Retro-Netplay-Engine")
    shutil.copytree(os.path.join(player, "Retro-Netplay-Player"), os.path.join(engine, "_internal", "player"), symlinks=True)
    dest = os.path.join(HERE, "dist-engine")
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    shutil.copytree(engine, os.path.join(dest, "Retro-Netplay-Engine"), symlinks=True)


def swap_ffmpeg(work):
    """Electron's own libffmpeg decodes H.264 and AAC (a patent grey area this app keeps out, see build-player.sh).
    The interface only plays WAV sounds, so use Electron's codec-free build of it instead (checked against
    Electron's published checksums)."""
    version = subprocess.run(["node", "-p", "require('electron/package.json').version"], cwd=SHELL, check=True,
                             capture_output=True, text=True).stdout.strip()
    folder, lib, plat = FFMPEG[SYS]
    name = f"ffmpeg-v{version}-{plat}.zip"
    base = f"https://github.com/electron/electron/releases/download/v{version}/"
    path = os.path.join(work, name)
    urllib.request.urlretrieve(base + name, path)
    sums = urllib.request.urlopen(base + "SHASUMS256.txt").read().decode()
    want = next((l.split()[0] for l in sums.splitlines() if l.strip().endswith(name)), "")
    got = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if not want or want != got:
        raise SystemExit(f"{name}: checksum doesn't match Electron's ({got} vs {want or 'not listed'})")
    target = os.path.join(SHELL, "dist", folder, lib)
    if not os.path.exists(target):
        raise SystemExit(f"Electron's {lib} isn't where it should be: {target}")
    with zipfile.ZipFile(path) as z, open(target, "wb") as f:
        f.write(z.read(lib))
    print(f"codec-free {lib} from {name} (checksum ok)")


def zip_folder(src, name, extra):
    """zip <src> as Retro-Netplay/ (plus extra files at its top), keeping symbolic links as links."""
    stage = tempfile.mkdtemp()
    top = os.path.join(stage, "Retro-Netplay")
    shutil.copytree(src, top, symlinks=True)
    for f, as_name in extra:
        shutil.copy(f, os.path.join(top, as_name))
    dest = os.path.join(OUT, name)
    if os.path.exists(dest):
        os.remove(dest)
    if SYS == "win":
        shutil.make_archive(dest[:-4], "zip", stage, "Retro-Netplay")
    else:
        run(["zip", "-qry", dest, "Retro-Netplay"], cwd=stage)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--player", required=True, help="folder holding Retro-Netplay-Player (from build-player.sh)")
    ap.add_argument("--sdlpy", default="", help="folder with the sdl2 and sdl2dll Python packages (Linux)")
    a = ap.parse_args()
    work = tempfile.mkdtemp()
    os.makedirs(OUT, exist_ok=True)
    build_engine(work, os.path.abspath(a.player), a.sdlpy)
    run([NPM, "ci", "--no-audit", "--no-fund"], cwd=SHELL)
    shutil.rmtree(os.path.join(SHELL, "dist"), ignore_errors=True)
    opts = ["--publish", "never"]
    if SYS == "mac":     # (a Mac app icon must be at least 512 pixels)
        big = os.path.join(work, "icon-512.png")
        run(["sips", "-z", "512", "512", os.path.join(HERE, "icon-256.png"), "--out", big])
        opts += ["-c.mac.icon=" + big, "-c.mac.identity=null", "--arm64"]
    run([NPX, "electron-builder", "--" + SYS, "dir"] + opts, cwd=SHELL)
    swap_ffmpeg(work)
    readme = os.path.join(HERE, "packaging", "README-linux.txt")
    lic = os.path.join(ROOT, "LICENSE")
    if SYS == "linux":
        run([NPX, "electron-builder", "--linux", "AppImage", "--prepackaged", "dist/linux-unpacked"] + opts, cwd=SHELL)
        shutil.copy(os.path.join(SHELL, "dist", "Retro-Netplay-x86_64.AppImage"), OUT)
        zip_folder(os.path.join(SHELL, "dist", "linux-unpacked"), "Retro-Netplay-linux-x86_64.zip",
                   [(readme, "README.txt"), (lic, "LICENSE.txt")])
    elif SYS == "win":
        zip_folder(os.path.join(SHELL, "dist", "win-unpacked"), "Retro-Netplay-windows-x86_64.zip", [(lic, "LICENSE.txt")])
    else:
        app = os.path.join(SHELL, "dist", "mac-arm64", "Retro-Netplay.app")
        # Apple Silicon only runs signed code: sign it again as a whole (ad hoc) after swapping the library
        run(["codesign", "--force", "--deep", "--sign", "-", app])
        run(["codesign", "--verify", "--deep", "--strict", app])
        dest = os.path.join(OUT, "Retro-Netplay-mac-arm64.zip")
        if os.path.exists(dest):
            os.remove(dest)
        run(["ditto", "-c", "-k", "--keepParent", app, dest])
        shutil.copy(lic, os.path.join(work, "LICENSE.txt"))
        run(["zip", "-q", dest, "LICENSE.txt"], cwd=work)
    print("\n".join(sorted(os.listdir(OUT))))


if __name__ == "__main__":
    main()
