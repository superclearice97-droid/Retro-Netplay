#!/bin/sh
# Starts Netplay Matchmaker on Linux (and macOS).
cd "$(dirname "$0")" || exit 1
if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "The window toolkit (tkinter) is missing. Install it, then run this again:"
  echo "  Debian / Ubuntu / Mint:  sudo apt install python3-tk"
  echo "  Fedora:                  sudo dnf install python3-tkinter"
  echo "  Arch / Manjaro:          sudo pacman -S tk"
  exit 1
fi
# A private Python environment (keeps system packages working, adds pygame for controllers).
VENV="$HOME/.netplay-matchmaker/venv"
[ -d "$VENV" ] || python3 -m venv --system-site-packages "$VENV" 2>/dev/null
if [ -x "$VENV/bin/python" ]; then
  "$VENV/bin/python" -c "import pygame" 2>/dev/null || "$VENV/bin/python" -m pip install --quiet pygame >/dev/null 2>&1
  exec "$VENV/bin/python" app.py "$@"
fi
exec python3 app.py "$@"
