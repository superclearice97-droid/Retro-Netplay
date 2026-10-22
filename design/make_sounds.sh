#!/bin/sh
# Makes the app's interface sounds with earshot (https://github.com/Leonxlnx/earshot, MIT licence): every sound is
# rendered from earshot's sound bank, which is code, not recordings. Then: mono, 16-bit, quieter (UI sounds).
# Needs: a checkout of earshot in EARSHOT and a Python with numpy, scipy, soundfile and soxr.
#   EARSHOT=~/earshot PY=python3 sh design/make_sounds.sh
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/desktop/sounds"
PY="${PY:-python3}"
mkdir -p "$OUT" "$ROOT/android/app/src/main/res/raw"
r() { (cd "$EARSHOT" && PYTHONPATH=. "$PY" -m earshot sfx render "$1" -o "$OUT/$2.wav" --seed 7); }
r hover_tick/mid move                  # moving through the game list
r fit/whoosh.short/bamboo_swish open   # a game's title card opens
r pro_notify/ding_2045 play            # Play
r pro_logo_sting/taps_tone found       # an opponent was found
r click/soft back                      # closing the card / leaving the queue
"$PY" - "$OUT" "$ROOT/android/app/src/main/res/raw" <<'PY'
import glob, os, shutil, sys
import soundfile as sf
out, raw = sys.argv[1], sys.argv[2]
for f in sorted(glob.glob(os.path.join(out, "*.wav"))):
    d, sr = sf.read(f)
    if d.ndim > 1:
        d = d.mean(axis=1)
    sf.write(f, d * 0.35, sr, subtype="PCM_16")
    shutil.copy(f, os.path.join(raw, "ui_" + os.path.basename(f)))
PY
ls -la "$OUT"
