#!/bin/sh
# Opens the packaged app (the AppImage) on a virtual screen and takes screenshots:
#   first-start (the welcome page); arcade (a set-up app in its own lobby); room (the same app after opening a room
#   through the engine's local API, as the interface does).
# Usage: smoke-test.sh <AppImage> <output folder>
set -u
APP="$(readlink -f "$1")"; OUT="$(mkdir -p "$2" && cd "$2" && pwd)"
WORK="$(mktemp -d)"
(cd "$WORK" && "$APP" --appimage-extract >/dev/null)        # unpack once (instead of on every start)
RUN="$WORK/squashfs-root/AppRun"
EXTRA=""; [ "$(id -u)" = 0 ] && EXTRA="--no-sandbox"      # (Chromium won't run as root with its sandbox)

cat > "$WORK/scene.sh" <<'EOF'
# runs inside xvfb-run: scene.sh <app> <extra> <home> <out> <name> [<room json>]
APP=$1; EXTRA=$2; export HOME=$3; OUT=$4; NAME=$5; ROOM=${6:-}
export XDG_CONFIG_HOME="$HOME/.config"      # (CI machines set their own; the app's log must land in this home)
"$APP" $EXTRA >"$HOME/app.log" 2>&1 &
LOG="$HOME/.config/Retro-Netplay/engine.log"
for _ in $(seq 1 60); do grep -q RETRO_NETPLAY_UI "$LOG" 2>/dev/null && break; sleep 1; done
if ! grep -q RETRO_NETPLAY_UI "$LOG" 2>/dev/null; then echo "$NAME: the engine didn't start"; cat "$LOG" "$HOME/app.log" 2>/dev/null | tail -30; exit 1; fi
sleep 7
import -window root "$OUT/$NAME.png"
if [ -n "$ROOM" ]; then
  U=$(sed -n 's/.*RETRO_NETPLAY_UI \(http[^ ]*\).*/\1/p' "$LOG" | head -1); B=${U%%/#*}; T=${U##*#t=}
  curl -s -H "X-Token: $T" -H "Content-Type: application/json" -d "$ROOM" "$B/api/do"; echo
  sleep 4
  import -window root "$OUT/room.png"
fi
pkill -P $$ 2>/dev/null
exit 0
EOF

fail=0
H1="$(mktemp -d)"
xvfb-run -a -s "-screen 0 1360x880x24" sh "$WORK/scene.sh" "$RUN" "$EXTRA" "$H1" "$OUT" first-start || fail=1

H2="$(mktemp -d)"
mkdir -p "$H2/.netplay-matchmaker" "$H2/Games/GBA" "$H2/bin" "$H2/.config/retroarch/cores"
for g in "Sonic Battle (USA)" "Mario Kart - Super Circuit (USA)"; do : > "$H2/Games/GBA/$g.gba"; done
printf '#!/bin/sh\nsleep 60\n' > "$H2/bin/retroarch"; chmod +x "$H2/bin/retroarch"; : > "$H2/.config/retroarch/cores/mgba_libretro.so"
cat > "$H2/.netplay-matchmaker/config.json" <<CFG
{"setup_done": true, "age_ok": true, "my_name": "Sam", "game_folders": ["$H2/Games"], "paths": {"retroarch": "$H2/bin/retroarch"},
 "auto_ports": false, "lobby_port": 17777, "lobby": "127.0.0.1:17777"}
CFG
xvfb-run -a -s "-screen 0 1360x880x24" sh "$WORK/scene.sh" "$RUN" "$EXTRA" "$H2" "$OUT" arcade \
  '{"action": "post", "game": 0, "seats": 3, "note": "Smoke test room", "auto": true}' || fail=1

for s in first-start arcade room; do [ -s "$OUT/$s.png" ] && echo "shot: $s" || { echo "MISSING: $s"; fail=1; }; done
rm -rf "$WORK"
echo "smoke test: $([ $fail = 0 ] && echo ok || echo FAILED)"
exit $fail
