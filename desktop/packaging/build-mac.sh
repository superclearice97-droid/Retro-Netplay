#!/bin/sh
# Builds the Mac download: dist/Retro-Netplay-mac-arm64.zip with Retro-Netplay.app (Apple Silicon Macs).
# The app opens netplay:// links (its Info.plist says so) and carries the player window with its own GStreamer (from
# Homebrew) and SDL (from pysdl2-dll); the player decodes the video with macOS's own decoder (VideoToolbox).
# Needs: Homebrew with gstreamer, libnice-gstreamer and pygobject3 (the player); a Python with tkinter for the app
# (python3, or the one in APP_PYTHON). Not signed with an Apple developer certificate: see README.txt for how to open it.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"     # desktop/
ROOT="$(cd "$HERE/.." && pwd)"
OUT="$ROOT/dist"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
APY="${APP_PYTHON:-python3}"
cd "$HERE"
PLAYER_PYTHON="${PLAYER_PYTHON:-$(brew --prefix)/bin/python3}" sh "$HERE/packaging/build-player.sh" "$WORK/player"

"$APY" -m pip install --quiet pyinstaller==6.11.1 pygame==2.6.1 pillow==11.0.0
mkdir -p "$WORK/notices"
cp "$ROOT/LICENSE" "$ROOT/NOTICE.md" "$ROOT/PRIVACY.md" "$ROOT/TERMS.md" "$WORK/notices/"
"$APY" -m PyInstaller --noconfirm --clean --windowed --name Retro-Netplay --osx-bundle-identifier net.netplay.retro-netplay \
  --distpath "$WORK/dist" --workpath "$WORK/build" --specpath "$WORK" \
  --add-data "$HERE/icon-256.png:." --add-data "$HERE/sounds:sounds" --add-data "$WORK/notices:notices" \
  --hidden-import server --hidden-import sessions --hidden-import stream_signal --hidden-import wizard --hidden-import social \
  --hidden-import pygame --hidden-import titlecard --hidden-import gameinfo --hidden-import PIL.ImageTk \
  --exclude-module stream_host --exclude-module stream_guest --exclude-module portal_capture --exclude-module gi \
  "$HERE/app.py"

APP="$WORK/dist/Retro-Netplay.app"
cp -a "$WORK/player/Retro-Netplay-Player" "$APP/Contents/Resources/player"     # runtime.guest_commands() looks here
# netplay:// links open this app (macOS reads this when the app is first opened or copied to Applications)
"$APY" - "$APP/Contents/Info.plist" <<'PY'
import plistlib, sys
p = sys.argv[1]
with open(p, "rb") as f:
    info = plistlib.load(f)
info["CFBundleURLTypes"] = [{"CFBundleURLName": "Retro-Netplay link", "CFBundleURLSchemes": ["netplay"]}]
info["CFBundleShortVersionString"] = "1.0"
info["NSHighResolutionCapable"] = True
with open(p, "wb") as f:
    plistlib.dump(info, f)
PY
# Apple Silicon only runs signed code: sign it again as a whole (ad hoc, no developer certificate) after the changes
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"
mkdir -p "$OUT"
cp "$ROOT/LICENSE" "$WORK/dist/LICENSE.txt"
rm -f "$OUT/Retro-Netplay-mac-arm64.zip"
(cd "$WORK/dist" && ditto -c -k --keepParent Retro-Netplay.app "$OUT/Retro-Netplay-mac-arm64.zip" &&
  zip -q "$OUT/Retro-Netplay-mac-arm64.zip" LICENSE.txt)
ls -la "$OUT"
