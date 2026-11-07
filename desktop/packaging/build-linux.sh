#!/bin/sh
# Builds the Linux download: dist/Retro-Netplay-linux-x86_64.zip and dist/Retro-Netplay-x86_64.AppImage
# (the Electron window around the engine, with the ready-made player; see pack_app.py).
# Needs: Python 3.10+, Node 20+, a Python with GStreamer for the player (PLAYER_PYTHON), and internet.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"     # desktop/
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
python3 -m pip install --quiet pyinstaller==6.11.1
# the player window that shows a friend's stream: ready-made, with its own GStreamer and SDL (built with the system's
# Python, which has GStreamer), plus its script and SDL for the computer's own Python as the fallback
PLAYER_PYTHON="${PLAYER_PYTHON:-/usr/bin/python3}" sh "$HERE/packaging/build-player.sh" "$WORK/player"
python3 -m pip install --quiet --target "$WORK/sdlpy" pysdl2==0.9.17 pysdl2-dll==2.30.2
find "$WORK/sdlpy/sdl2dll/dll" -type f ! -name 'libSDL2-2.0*' ! -name 'libSDL2_ttf*' -delete   # only SDL2 and its text
python3 "$HERE/packaging/pack_app.py" --player "$WORK/player" --sdlpy "$WORK/sdlpy"
