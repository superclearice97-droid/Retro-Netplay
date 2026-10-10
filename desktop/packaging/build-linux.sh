#!/bin/sh
# Builds the Linux download: dist/Retro-Netplay-linux-x86_64.zip and dist/Retro-Netplay-x86_64.AppImage.
# Needs: Python 3.10+ with tkinter, and internet for pip and appimagetool. Run from anywhere.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"     # desktop/
ROOT="$(cd "$HERE/.." && pwd)"
OUT="$ROOT/dist"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$HERE"
python3 -m pip install --quiet pyinstaller==6.11.1 pygame==2.6.1

mkdir -p "$WORK/notices"
cp "$ROOT/LICENSE" "$ROOT/NOTICE.md" "$ROOT/PRIVACY.md" "$ROOT/TERMS.md" "$WORK/notices/"

python3 -m PyInstaller --noconfirm --clean --windowed --name Retro-Netplay \
  --distpath "$WORK/dist" --workpath "$WORK/build" --specpath "$WORK" \
  --add-data "$HERE/web:web" \
  --add-data "$HERE/icon-256.png:." \
  --add-data "$HERE/stream_host.py:hostpy" \
  --add-data "$HERE/uinput_pad.py:hostpy" \
  --add-data "$HERE/portal_capture.py:hostpy" \
  --add-data "$WORK/notices:notices" \
  --hidden-import server --hidden-import sessions --hidden-import wizard --hidden-import social \
  --hidden-import pygame \
  --exclude-module stream_host --exclude-module portal_capture --exclude-module gi \
  "$HERE/app.py"

APP="$WORK/dist/Retro-Netplay"
mkdir -p "$OUT"

# 1) zip: unzip, open the folder, double-click Retro-Netplay
PKG="$WORK/zip/Retro-Netplay"
mkdir -p "$PKG"
cp -a "$APP/." "$PKG/"
cp "$HERE/packaging/README-linux.txt" "$PKG/README.txt"
cp "$ROOT/LICENSE" "$PKG/LICENSE.txt"
(cd "$WORK/zip" && rm -f "$OUT/Retro-Netplay-linux-x86_64.zip" && zip -qry "$OUT/Retro-Netplay-linux-x86_64.zip" Retro-Netplay)

# 2) AppImage: one file
AD="$WORK/AppDir"
mkdir -p "$AD/usr/lib"
cp -a "$APP" "$AD/usr/lib/retro-netplay"
cp "$HERE/icon-256.png" "$AD/retro-netplay.png"
cat > "$AD/retro-netplay.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Retro-Netplay
Comment=Play your retro games online with friends
Exec=Retro-Netplay %u
Icon=retro-netplay
Terminal=false
Categories=Game;
MimeType=x-scheme-handler/netplay;
DESK
cat > "$AD/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/retro-netplay/Retro-Netplay" "$@"
RUN
chmod +x "$AD/AppRun"
TOOL="$WORK/appimagetool"
curl -sSL -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x "$TOOL"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$AD" "$OUT/Retro-Netplay-x86_64.AppImage"
ls -la "$OUT"
