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
case "$(uname -s)" in MINGW*|MSYS*) WIN=1 ;; *) WIN="" ;; esac
MAC=""; [ "$(uname -s)" != Darwin ] || MAC="$(brew --prefix)"     # macOS: Homebrew's GStreamer and SDL2
SRC="$HERE"                                  # (as the build's Python sees it: C:/... on Windows)
[ -z "$WIN" ] || SRC="$(cygpath -m "$HERE")"
PYI=6.11.1; [ -z "$MAC" ] || PYI=6.16.0     # (Homebrew's Python is newer than 6.11 supports)
"$PY" -c "import PyInstaller" 2>/dev/null || "$PY" -m pip install --quiet "pyinstaller==$PYI"
"$PY" -m pip install --quiet pysdl2==0.9.17
# SDL2 + SDL2_ttf: the pysdl2-dll package on Linux; on Windows (MSYS2) and macOS (Homebrew) their own SDL2 packages
[ -n "$WIN$MAC" ] || "$PY" -m pip install --quiet pysdl2-dll==2.30.2

cat > "$WORK/player.spec" <<SPEC
# (written by build-player.sh)
import glob, os, shutil
# Linux: VA-API, NVIDIA; Windows: Direct3D 11/12 (the graphics card through Windows' own DXVA); macOS: VideoToolbox
PLUGINS = ["coreelements", "app", "videoconvert", "videoconvertscale", "webrtc", "nice", "dtls", "srtp", "sctp",
           "rtp", "rtpmanager", "videoparsersbad", "opus", "audioconvert", "audioresample", "autodetect",
           "pulseaudio", "alsa", "va", "nvcodec", "typefindfunctions", "d3d11", "d3d12", "wasapi", "wasapi2",
           "directsound", "applemedia", "osxaudio"]
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files
if "$WIN":   # MSYS2's SDL2 and SDL2_ttf next to the player (PySDL2 is told to look there)
    import sys
    sdl = [(f, ".") for n in ("SDL2.dll", "SDL2_ttf.dll") for f in [os.path.join(sys.base_prefix, "bin", n)]
           if os.path.exists(f)]
    sdl_data = []
elif "$MAC":  # Homebrew's SDL2 and SDL2_ttf next to the player (PySDL2 is told to look there)
    sdl = [(f, ".") for n in ("libSDL2-2.0.0.dylib", "libSDL2_ttf-2.0.0.dylib") for f in [os.path.join("$MAC", "lib", n)]
           if os.path.exists(f)]
    sdl_data = []
else:
    sdl = [b for b in collect_dynamic_libs("sdl2dll") if "SDL2-2.0" in b[0] or "SDL2_ttf" in b[0]]
    sdl_data = collect_data_files("sdl2dll")
# NSS's crypto modules: libsrtp (the video's encryption) uses NSS on Debian/Ubuntu, and NSS loads these at run time from
# next to libnss3; without them SRTP can't start and no video arrives (only the controller channel works)
nss = [(f, ".") for d in ("/usr/lib/x86_64-linux-gnu/nss", "/usr/lib64/nss", "/usr/lib/nss")
       for n in ("libsoftokn3", "libfreebl3", "libfreeblpriv3") for f in glob.glob(f"{d}/{n}.*")]
a = Analysis([r"$SRC/stream_guest.py"], pathex=[r"$SRC"],
             binaries=sdl + nss,
             datas=[(r"$SRC/fonts", "fonts")] + sdl_data,
             hiddenimports=["stream_signal", "stream_host", "uinput_pad", "sdl2", "sdl2.sdlttf"] + ([] if "$WIN$MAC" else ["sdl2dll"]),
             hooksconfig={"gstreamer": {"include_plugins": PLUGINS},
                          "gi": {"icons": [], "themes": [], "languages": ["en_US"]}},
             excludes=["tkinter", "pygame", "cryptography"])
# Linux: use the computer's own VA-API library (it must match the computer's graphics drivers)
if not "$WIN$MAC":
    a.binaries = [b for b in a.binaries if not os.path.basename(b[0]).startswith("libva")]
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
elif command -v pacman >/dev/null 2>&1; then     # Windows (MSYS2): https://packages.msys2.org/base/<package>
  : > "$LIC/PACKAGES.txt"
  find "$OUT/Retro-Netplay-Player/_internal" -iname '*.dll' -type f | while read -r f; do
    src="$(command -v "$(basename "$f")" 2>/dev/null || true)"
    [ -n "$src" ] || src="$(ls /ucrt64/lib/gstreamer-1.0/"$(basename "$f")" 2>/dev/null || true)"
    [ -n "$src" ] || continue
    pkg="$(pacman -Qqo "$src" 2>/dev/null | head -1)"
    [ -n "$pkg" ] || continue
    grep -q "^$pkg " "$LIC/PACKAGES.txt" && continue
    echo "$pkg $(pacman -Q "$pkg" | cut -d' ' -f2)" >> "$LIC/PACKAGES.txt"
    [ -d "/ucrt64/share/licenses/${pkg#mingw-w64-ucrt-x86_64-}" ] && cp -r "/ucrt64/share/licenses/${pkg#mingw-w64-ucrt-x86_64-}" "$LIC/$pkg"
  done
  sort -o "$LIC/PACKAGES.txt" "$LIC/PACKAGES.txt"
elif [ -n "$MAC" ]; then         # macOS (Homebrew): https://formulae.brew.sh/formula/<formula>, the keg keeps its licence
  : > "$LIC/PACKAGES.txt"
  CELLAR="$(brew --cellar)"
  find "$OUT/Retro-Netplay-Player/_internal" \( -name '*.dylib' -o -name '*.so' \) -type f | while read -r f; do
    src="$(ls "$CELLAR"/*/*/lib/"$(basename "$f")" "$CELLAR"/*/*/lib/gstreamer-1.0/"$(basename "$f")" 2>/dev/null | head -1 || true)"
    [ -n "$src" ] || continue
    rel="${src#"$CELLAR"/}"; pkg="${rel%%/*}"; ver="$(echo "$rel" | cut -d/ -f2)"
    grep -q "^$pkg " "$LIC/PACKAGES.txt" && continue
    echo "$pkg $ver" >> "$LIC/PACKAGES.txt"
    mkdir -p "$LIC/$pkg"
    for l in "$CELLAR/$pkg/$ver"/LICEN[CS]E* "$CELLAR/$pkg/$ver"/COPYING* "$CELLAR/$pkg/$ver"/NOTICE*; do
      [ -f "$l" ] && cp "$l" "$LIC/$pkg/"
    done
  done
  sort -o "$LIC/PACKAGES.txt" "$LIC/PACKAGES.txt"
fi
for f in "$("$PY" -c 'import sdl2dll, os; print(os.path.dirname(sdl2dll.__file__))' 2>/dev/null)"/../pysdl2_dll-*.dist-info/LICENSE*; do
  [ -f "$f" ] && cp "$f" "$LIC/pysdl2-dll-$(basename "$f")"
done
"$OUT/Retro-Netplay-Player/Retro-Netplay-Player" --check || true    # (.exe on Windows)
du -sh "$OUT/Retro-Netplay-Player"
