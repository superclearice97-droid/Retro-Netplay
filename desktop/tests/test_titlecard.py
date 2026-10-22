"""The title card's pictures without a screen: the ported MeshGradient moves and uses the palette, the CRT mask darkens
scanlines, the gameplay window pans and crossfades screenshots, box-art colours make a dark-to-light palette, and
the Lucide icons and their motions are well formed. Skips without Pillow."""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
try:
    from PIL import Image, ImageChops, ImageDraw
except ImportError:
    print("SKIP (needs Pillow)"); sys.exit(0)
import titlecard as T
from icons import ICONS, MOTIONS

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info and not ok else "")); ok or fails.append(name)

stops = [T._rgb(c) for c in T.THEMES["gba"]]
m = T.Mesh(stops)
a, b = m.render(0.0), m.render(20.0)
check("mesh gradient renders at its small size", a.size == (64, 36))
check("mesh gradient moves over time", ImageChops.difference(a, b).getbbox() is not None)
cols = {c for _, c in a.getcolors(64 * 36)}
lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
check("mesh gradient stays inside the palette's light range", all(lum(stops[0]) - 1 <= lum(c) <= lum(stops[-1]) + 1 for c in cols))
check("mesh gradient blends (many colours, not flat)", len(cols) > 40, len(cols))

mask = T.crt_mask(320, 180, 60, 0.3, 0.8).convert("L")
col = [mask.getpixel((160, y)) for y in range(60, 120)]
check("CRT mask has scanlines (light and dark rows)", max(col) - min(col) > 40, (min(col), max(col)))
check("CRT vignette: corners darker than the centre", mask.getpixel((2, 2)) < mask.getpixel((160, 90)))

d = tempfile.mkdtemp()
paths = []
for k, colr in enumerate([(200, 40, 40), (40, 200, 40)]):
    im = Image.new("RGB", (240, 160), colr)
    ImageDraw.Draw(im).rectangle([100, 60, 140, 100], fill=(255, 255, 255))
    p = os.path.join(d, f"s{k}.png"); im.save(p); paths.append(p)
sl = T.Slides(paths + [os.path.join(d, "missing.png")], 300, 170)
check("missing screenshots are skipped", len(sl.imgs) == 2)
f0, f1, fx = sl.frame(0.5), sl.frame(3.5), sl.frame(T.SHOW - T.FADE / 2)
check("gameplay window is the right size", f0.size == (300, 170))
check("gameplay window pans (Ken Burns)", ImageChops.difference(f0, f1).getbbox() is not None)
r0, g0 = f0.getpixel((20, 20))[:2]
rx, gx = fx.getpixel((20, 20))[:2]
check("gameplay window crossfades to the next screenshot", r0 > g0 and abs(rx - gx) < r0 - g0, (f0.getpixel((20, 20)), fx.getpixel((20, 20))))
check("no screenshots: no gameplay picture", T.Slides([], 100, 60).frame(1.0) is None)

box = Image.new("RGB", (100, 140), (20, 60, 200))
ImageDraw.Draw(box).rectangle([0, 0, 100, 40], fill=(250, 220, 30))
pal = T.palette_from(box, T.THEMES["ps2"])
check("box-art palette: five colours, dark to light", len(pal) == 5 and [lum(c) for c in pal] == sorted(lum(c) for c in pal), pal)
check("box-art palette: dark enough for light text at the dark end", lum(pal[0]) < 60, pal)

for key in ("star", "calendar", "users", "tag", "building", "gamepad", "go", "seek", "exit", "image", "film"):
    s = ICONS.get(key, [])
    check(f"Lucide icon {key} is on the 24x24 grid", s and all(-0.5 <= v <= 24.5 for st in s for p in st for v in p))
ok = True
for key, mo in MOTIONS.items():
    for part in mo["parts"]:
        ok &= all(0 <= i < len(ICONS[key]) for i in part["strokes"]) and part["keys"][0][0] == 0 and part["keys"][-1][0] == 1
check("icon motions point at real strokes and run from 0 to 1", ok)
check("icon motion ends where it started (users)", T._keys(MOTIONS["users"]["parts"][0]["keys"], 1.0) == (0, 0, 0, 1))

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
