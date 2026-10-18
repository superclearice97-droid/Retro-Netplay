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
{"setup_done": true, "menu_entry": true, "server_host": "127.0.0.1", "run_server": true, "server_port": 17777, "game_folders": ["$H2/Games"],
 "games": [{"name": "Urban Reign", "system": "ps2", "rom": "/nope/Urban Reign.iso"}],
 "my_name": "Sam", "secret": "0123456789abcdef0123456789abcdef",
 "friends": [{"id": "aaaaaaaaaaaaaaaaaaaaaaaa", "name": "Bea", "priority": true, "join": true},
             {"id": "bbbbbbbbbbbbbbbbbbbbbbbb", "name": "Cy", "code": "K7M-4QZ", "pending": true}],
 "friend_requests": [{"id": "cccccccccccccccccccccccc", "name": "Dee", "code": "P2X-9HN"}]}
CFG
shot main 12 HOME="$H2"
grep -q "Retro-Netplay" "$H2/.local/share/applications/retro-netplay.desktop" 2>/dev/null \
  && echo "menu entry: ok" || { echo "menu entry: MISSING"; fail=1; }
shot friends 12 HOME="$H2" NETPLAY_SHOW=friends
shot settings 12 HOME="$H2" NETPLAY_SHOW=settings
shot controllers 12 HOME="$H2" NETPLAY_SHOW=controllers
shot address 12 HOME="$H2" NETPLAY_SHOW=address
shot card 12 HOME="$H2" NETPLAY_SHOW=card

# 2b) a window opened from Settings must take clicks (Settings holds all clicks while it's open):
#     open Streaming check from Settings, click its Close button with the real mouse, it must close.
env HOME="$H2" NETPLAY_SHOW=streamcheck xvfb-run -a -s "-screen 0 1280x900x24" sh -c "
  \"$APPIMG\" > \"$OUT/streamcheck.log\" 2>&1 &
  pid=\$!
  sleep 14
  w=\$(xdotool search --name '^Streaming check\$' | head -1)
  if [ -z \"\$w\" ]; then echo 'click test: Streaming check window not found' > \"$OUT/streamcheck.result\"; else
    eval \$(xdotool getwindowgeometry --shell \$w)
    import -window root \"$OUT/streamcheck.png\"
    xdotool mousemove \$((X + WIDTH - 50)) \$((Y + HEIGHT - 25)) click 1
    sleep 2
    if xdotool search --name '^Streaming check\$' >/dev/null 2>&1; then echo 'click test: FAILED, Close did nothing' > \"$OUT/streamcheck.result\";
    else echo 'click test: ok' > \"$OUT/streamcheck.result\"; fi
  fi
  kill \$pid 2>/dev/null; sleep 1; kill -9 \$pid 2>/dev/null; true"
cat "$OUT/streamcheck.result"
grep -q "click test: ok" "$OUT/streamcheck.result" || fail=1

# 3) the bundled matchmaker works from the packaged app's Python? (run_server path)
cat "$OUT"/*.log | head -50
exit $fail
