#!/bin/sh
# Builds the Mac download: dist/Retro-Netplay-mac-arm64.zip with Retro-Netplay.app (Apple Silicon Macs): the Electron
# window around the engine (see pack_app.py), with the player window and its own GStreamer (from Homebrew) and SDL
# (from pysdl2-dll); the player decodes video with macOS's own decoder (VideoToolbox). netplay:// links open the app.
# Needs: Homebrew with gstreamer, libnice-gstreamer and pygobject3 (the player), Python 3.10+ (APP_PYTHON), Node 20+.
# Signed ad hoc, not with an Apple developer certificate: see README.txt for how to open it.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"     # desktop/
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
APY="${APP_PYTHON:-python3}"
PLAYER_PYTHON="${PLAYER_PYTHON:-$(brew --prefix)/bin/python3}" sh "$HERE/packaging/build-player.sh" "$WORK/player"
"$APY" -m pip install --quiet pyinstaller==6.11.1
"$APY" "$HERE/packaging/pack_app.py" --player "$WORK/player"
