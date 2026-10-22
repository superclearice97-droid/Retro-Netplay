"""Where the app is running from: straight from the source folder, or as the packaged download (PyInstaller,
optionally inside an AppImage). Keeps every path and child process right in both cases."""
import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
SRC = Path(__file__).resolve().parent
# bundled data (icons, the stream host scripts) lives next to the modules in a packaged build
BASE = Path(getattr(sys, "_MEIPASS", SRC))
APP_ID = "retro-netplay"


def restore_child_env():
    """The packaged app points the library path at its own bundled libraries. Programs we start (RetroArch,
    the web browser, the stream host) must get the computer's normal environment back, or they can crash."""
    if not FROZEN:
        return
    for var in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        orig = os.environ.get(var + "_ORIG")
        if orig is not None:
            os.environ[var] = orig
        else:
            os.environ.pop(var, None)
    for var in ("TCL_LIBRARY", "TK_LIBRARY", "PYTHONHOME", "PYTHONPATH"):
        os.environ.pop(var + "_ORIG", None)
    # AppImage variables meant only for the app itself
    for var in ("PYTHONHOME", "PYTHONPATH"):
        os.environ.pop(var, None)


def launcher():
    """The command that starts this app again (for the app-menu entry and netplay:// links)."""
    if os.environ.get("APPIMAGE"):
        return os.environ["APPIMAGE"]
    if FROZEN:
        return sys.executable
    return str(SRC / "start.sh")


def host_script():
    """stream_host.py as a real file (it runs under the computer's own Python, which has GStreamer)."""
    p = BASE / "hostpy" / "stream_host.py"
    return p if p.exists() else SRC / "stream_host.py"


def host_python():
    """Python to run the stream host with. The packaged app can't carry GStreamer, so it uses the system's."""
    if not FROZEN:
        return sys.executable
    return shutil.which("python3") or shutil.which("python") or ""


def guest_commands():
    """Ways to start the player window (stream_guest.py), best first, as command lists. The download carries a
    ready-made player with its own GStreamer and SDL (it decodes on the graphics card); the computer's own Python and
    GStreamer come next (they may have a software decoder, which the download leaves out). From source: this Python."""
    out = []
    name = "Retro-Netplay-Player.exe" if sys.platform == "win32" else "Retro-Netplay-Player"
    for exe in (BASE / "player" / name, BASE.parent / "Resources" / "player" / name):    # (Mac: inside the .app)
        if exe.exists():
            out.append([str(exe)])
            break
    if not FROZEN:
        out.append([sys.executable, str(SRC / "stream_guest.py")])
    else:
        py, script = host_python(), BASE / "hostpy" / "stream_guest.py"
        if py and script.exists():
            out.append([py, str(script)])
    return out


def icon_file():
    return BASE / "icon-256.png"


def install_menu_entry(home=None):
    """Add Retro-Netplay to the app menu (Linux), and let netplay:// links open it. Returns a message."""
    if not sys.platform.startswith("linux"):
        return ""
    home = Path(home or Path.home())
    apps = home / ".local/share/applications"
    icons = home / ".local/share/icons/hicolor/256x256/apps"
    try:
        apps.mkdir(parents=True, exist_ok=True)
        icons.mkdir(parents=True, exist_ok=True)
        if icon_file().exists():
            shutil.copyfile(icon_file(), icons / f"{APP_ID}.png")
        exe = launcher()
        quoted = '"' + exe.replace("\\", "\\\\").replace('"', '\\"') + '"'
        (apps / f"{APP_ID}.desktop").write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Retro-Netplay\n"
            "Comment=Play your retro games online with friends\n"
            f"Exec={quoted} %u\n"
            f"Icon={APP_ID}\n"
            "Terminal=false\n"
            "Categories=Game;\n"
            "MimeType=x-scheme-handler/netplay;\n"
            "StartupWMClass=Retro-Netplay\n")
        old = apps / "netplay-matchmaker.desktop"     # from the older install script
        if old.exists():
            old.unlink()
        if shutil.which("update-desktop-database"):
            os.system(f'update-desktop-database "{apps}" >/dev/null 2>&1')
        if shutil.which("xdg-mime"):
            os.system(f'xdg-mime default {APP_ID}.desktop x-scheme-handler/netplay >/dev/null 2>&1')
        return "Added Retro-Netplay to your app menu."
    except OSError as e:
        return f"Couldn't add the app to your menu ({e})."


def register_link_handler():
    """Windows: let netplay:// links open this app (per user, no admin rights). Returns a message or ''."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        exe = sys.executable if FROZEN else str(Path(sys.executable).with_name("pythonw.exe"))
        cmd = f'"{exe}" "%1"' if FROZEN else f'"{exe}" "{SRC / "app.py"}" "%1"'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\netplay") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:Retro-Netplay link")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\netplay\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
        return ""
    except OSError as e:
        return f"Couldn't set up netplay:// links ({e})."


def menu_entry_points_here(home=None):
    p = Path(home or Path.home()) / ".local/share/applications" / f"{APP_ID}.desktop"
    try:
        return launcher() in p.read_text()
    except OSError:
        return False
