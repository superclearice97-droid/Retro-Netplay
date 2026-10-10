#!/bin/sh
# Opens the built Linux app on a virtual screen, takes screenshots, and fails if it crashed.
# Usage: smoke-test.sh path/to/Retro-Netplay-x86_64.AppImage outdir
set -u
APPIMG="$(readlink -f "$1")"; OUT="$(readlink -f "$2")"
mkdir -p "$OUT"
export APPIMAGE_EXTRACT_AND_RUN=1
fail=0

shot() {   # name, seconds, extra env...
  name="$1"; wait="$2"; shift 2
  env "$@" xvfb-run -a -s "-screen 0 1280x900x24" sh -c "
    \"$APPIMG\" > \"$OUT/$name.log\" 2>&1 &
    pid=\$!
    sleep $wait
    import -window root \"$OUT/$name.png\"
    xdotool search --name 'Retro-Netplay' >> \"$OUT/$name.windows\" 2>/dev/null
    kill \$pid 2>/dev/null; sleep 1; kill -9 \$pid 2>/dev/null; true"
  if grep -q "Traceback" "$OUT/$name.log"; then echo "CRASH in $name:"; cat "$OUT/$name.log"; fail=1; fi
  [ -s "$OUT/$name.png" ] || { echo "no screenshot for $name"; fail=1; }
}

# 1) first run: setup appears
H1="$(mktemp -d)"
shot first-run 12 HOME="$H1"

# 2) a set-up app with games and friends
H2="$(mktemp -d)"
mkdir -p "$H2/.netplay-matchmaker" "$H2/Games/GBA"
: > "$H2/Games/GBA/Sonic Battle (USA).gba"
: > "$H2/Games/GBA/Mario Kart - Super Circuit (USA).gba"
cat > "$H2/.netplay-matchmaker/config.json" <<CFG
{"setup_done": true, "menu_entry": true, "server_host": "203.0.113.5", "game_folders": ["$H2/Games"],
 "games": [{"name": "Urban Reign", "system": "ps2", "rom": "/nope/Urban Reign.iso"}],
 "my_name": "Sam", "secret": "0123456789abcdef0123456789abcdef",
 "friends": [{"id": "aaaaaaaaaaaaaaaaaaaaaaaa", "name": "Bea", "priority": true, "join": true},
             {"id": "bbbbbbbbbbbbbbbbbbbbbbbb", "name": "Cy"}]}
CFG
shot main 12 HOME="$H2"
grep -q "Retro-Netplay" "$H2/.local/share/applications/retro-netplay.desktop" 2>/dev/null \
  && echo "menu entry: ok" || { echo "menu entry: MISSING"; fail=1; }
shot friends 12 HOME="$H2" NETPLAY_SHOW=friends
shot settings 12 HOME="$H2" NETPLAY_SHOW=settings
shot controllers 12 HOME="$H2" NETPLAY_SHOW=controllers

# 3) the bundled matchmaker works from the packaged app's Python? (run_server path)
cat "$OUT"/*.log | head -50
exit $fail
