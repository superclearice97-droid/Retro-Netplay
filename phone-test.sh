#!/bin/sh
# Test everything that doesn't need your home computer, from a phone (Termux) or any computer.
#   sh phone-test.sh            starts a matchmaker + a pretend opponent (for the stream lane: matching only)
#   sh phone-test.sh native     pretend opponent for the emulator-netplay lane instead
# Needs only Python:  pkg install python   (Termux)
cd "$(dirname "$0")/desktop" || exit 1
command -v python3 >/dev/null 2>&1 || { echo "Python is missing. In Termux run:  pkg install python"; exit 1; }
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
LOG="${TMPDIR:-/tmp}/netplay-server.log"
python3 server.py 7777 > "$LOG" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT INT TERM
sleep 1
kill -0 $SERVER 2>/dev/null || { echo "The matchmaker didn't start:"; cat "$LOG"; exit 1; }
echo
echo "Matchmaker is running."
echo "  In the Android app set Settings > Matchmaker address to 127.0.0.1 and port 7777."
echo "     Game: system PlayStation 2, name  Urban Reign   (or use the 'native' option below)"
echo
if [ "$1" = "native" ]; then
  echo "Pretend opponent for GBA netplay. In the app add a GBA game named  Test Game"
  exec python3 fake_opponent.py --loop --lane native --game gba:test-game
fi
exec python3 fake_opponent.py --loop
