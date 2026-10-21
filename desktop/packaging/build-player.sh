#!/bin/sh
# Builds the player window (stream_guest.py) as a ready-made folder with its own GStreamer and SDL:
#   <out>/Retro-Netplay-Player/Retro-Netplay-Player
# Usage: build-player.sh <out dir>. Needs: a Python with PyGObject (gi), GStreamer 1.20+ (webrtcbin, nice) and venv:
# python3, or the one in PLAYER_PYTHON.
#
# Not included on purpose: software H.264 decoders (gst-libav / avdec_h264, openh264). Shipping one is a patent
# grey area, so the bundled player decodes on the graphics card (VA-API for Intel/AMD, NVIDIA's decoder) through the
# computer's own drivers, and the app falls back to the computer's own GStreamer when that has a software decoder.
# libva is not bundled either: the system's copy must match the system's graphics drivers.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"     # desktop/
OUT="$(mkdir -p "$1" && cd "$1" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
"${PLAYER_PYTHON:-python3}" -m venv --system-site-packages "$WORK/venv"       # (sees the system's PyGObject / GStreamer)
PY="$WORK/venv/bin/python"
"$PY" -m pip install --quiet pyinstaller==6.11.1 pysdl2==0.9.17 pysdl2-dll==2.30.2

cat > "$WORK/player.spec" <<SPEC
# (written by build-player.sh)
PLUGINS = ["coreelements", "app", "videoconvert", "videoconvertscale", "webrtc", "nice", "dtls", "srtp", "sctp",
           "rtp", "rtpmanager", "videoparsersbad", "opus", "audioconvert", "audioresample", "autodetect",
           "pulseaudio", "alsa", "va", "nvcodec", "typefindfunctions"]
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files
a = Analysis([r"$HERE/stream_guest.py"], pathex=[r"$HERE"],
             binaries=[b for b in collect_dynamic_libs("sdl2dll") if "SDL2-2.0" in b[0] or "SDL2_ttf" in b[0]],
             datas=[(r"$HERE/fonts", "fonts")] + collect_data_files("sdl2dll"),
             hiddenimports=["stream_signal", "stream_host", "uinput_pad", "sdl2", "sdl2.sdlttf", "sdl2dll"],
             hooksconfig={"gstreamer": {"include_plugins": PLUGINS},
                          "gi": {"icons": [], "themes": [], "languages": ["en_US"]}},
             excludes=["tkinter", "pygame", "cryptography"])
# use the computer's own VA-API library (it must match the computer's graphics drivers)
a.binaries = [b for b in a.binaries if not b[0].split("/")[-1].startswith("libva")]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Retro-Netplay-Player", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="Retro-Netplay-Player")
SPEC
"$PY" -m PyInstaller --noconfirm --clean --distpath "$OUT" --workpath "$WORK/build" "$WORK/player.spec"
# only SDL2 and its text library are needed (the SDL package also carries image and sound libraries)
find "$OUT/Retro-Netplay-Player" -path '*sdl2dll*' -type f -name 'lib*.so*' ! -name 'libSDL2-2.0*' ! -name 'libSDL2_ttf*' -delete
for lib in libavif libdav1d libwebp libwebpdemux libtiff libxmp libgme libwavpack libopusfile; do   # (they were for those)
  rm -f "$OUT/Retro-Netplay-Player/_internal/$lib".so*
done
# what is inside, for the record: no software H.264 decoder may be in here
if find "$OUT/Retro-Netplay-Player" -name '*gstlibav*' -o -name '*openh264*' -o -name 'libavcodec*' -o -name '*x264*' | grep -q .; then
  echo "a software H.264 codec ended up in the player bundle; refusing"; exit 1
fi
# the licence of every bundled library, and which distribution package (and version) it came from, so anyone can get
# the exact source code (LGPL): apt source <package>=<version>, or https://launchpad.net/ubuntu/+source/<package>
LIC="$OUT/Retro-Netplay-Player/licenses"
mkdir -p "$LIC"
cp "$HERE/fonts/DejaVu-LICENSE.txt" "$LIC/"
if command -v dpkg >/dev/null 2>&1; then
  : > "$LIC/PACKAGES.txt"
  find "$OUT/Retro-Netplay-Player/_internal" -name '*.so*' -type f | while read -r f; do
    base="$(basename "$f")"
    pkg="$(dpkg -S "*/$base" 2>/dev/null | grep -v "^diversion" | head -1 | cut -d: -f1 | cut -d, -f1)"
    [ -n "$pkg" ] || continue
    grep -q "^$pkg " "$LIC/PACKAGES.txt" && continue
    echo "$pkg $(dpkg-query -W -f='${Version} (source: ${source:Package})' "$pkg" 2>/dev/null)" >> "$LIC/PACKAGES.txt"
    [ -f "/usr/share/doc/$pkg/copyright" ] && cp "/usr/share/doc/$pkg/copyright" "$LIC/$pkg.txt"
  done
  sort -o "$LIC/PACKAGES.txt" "$LIC/PACKAGES.txt"
fi
for f in "$("$PY" -c 'import sdl2dll, os; print(os.path.dirname(sdl2dll.__file__))' 2>/dev/null)"/../pysdl2_dll-*.dist-info/LICENSE*; do
  [ -f "$f" ] && cp "$f" "$LIC/pysdl2-dll-$(basename "$f")"
done
"$OUT/Retro-Netplay-Player/Retro-Netplay-Player" --check || true
du -sh "$OUT/Retro-Netplay-Player"
