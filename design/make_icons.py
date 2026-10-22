#!/usr/bin/env python3
"""Draws Retro-Netplay's icon set once and writes it for every platform.

Each icon is a few strokes on a 24x24 grid, drawn with round caps at a constant width. A stroke is a
list of points, so the same data animates as "draw on" in the Android app (Icons.java) and the Linux
app (icons.py); icons.json keeps the raw strokes. These shapes are our
own drawings; no third-party icon files are used.

Run:  python3 design/make_icons.py     (from the repo root)
"""
import json, math, os

def line(*pts): return [list(map(float, p)) for p in pts]
def arc(cx, cy, r, a0, a1, n=None):
    n = n or max(6, int(abs(a1 - a0) / 12))
    return [[cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))] for i in range(n + 1)]
def circle(cx, cy, r): return arc(cx, cy, r, -90, 270, 32)
def quad(p0, c, p1, n=8):
    return [[(1-t)**2*p0[0] + 2*(1-t)*t*c[0] + t*t*p1[0], (1-t)**2*p0[1] + 2*(1-t)*t*c[1] + t*t*p1[1]]
            for t in [i / n for i in range(n + 1)]]
def stadium(cx, cy, half, r, deg):
    a = math.radians(deg); ux, uy = math.cos(a), math.sin(a); nx, ny = -uy, ux
    c1 = (cx - ux * half, cy - uy * half); c2 = (cx + ux * half, cy + uy * half)
    pts = arc(c1[0], c1[1], r, deg + 90, deg + 270, 8) + arc(c2[0], c2[1], r, deg - 90, deg + 90, 8)
    return pts + [pts[0]]
def join(*parts):
    out = []
    for p in parts: out += p
    return out

ICONS = {
  "search":  [circle(10, 10, 6), line((14.5, 14.5), (20, 20))],
  "close":   [line((6, 6), (18, 18)), line((18, 6), (6, 18))],
  "plus":    [line((12, 5), (12, 19)), line((5, 12), (19, 12))],
  "trash":   [line((5, 7), (19, 7)), line((10, 7), (10, 5), (14, 5), (14, 7)),
              line((7, 7), (8, 19), (16, 19), (17, 7)), line((10, 11), (10, 15)), line((14, 11), (14, 15))],
  "link":    [stadium(8.6, 15.4, 2.2, 3.2, -45), stadium(15.4, 8.6, 2.2, 3.2, -45), line((9.8, 14.2), (14.2, 9.8))],
  "share":   [circle(6, 12, 2.6), circle(18, 5.5, 2.6), circle(18, 18.5, 2.6),
              line((8.3, 10.8), (15.7, 6.7)), line((8.3, 13.2), (15.7, 17.3))],
  "gear":    [circle(12, 12, 2.4), circle(12, 12, 5.6)] +
             [line((12 + 7.6 * math.cos(math.radians(a)), 12 + 7.6 * math.sin(math.radians(a))),
                   (12 + 9.8 * math.cos(math.radians(a)), 12 + 9.8 * math.sin(math.radians(a))))
              for a in range(0, 360, 45)],
  "pad":     [join(quad((7, 7.5), (12, 6.5), (17, 7.5)), quad((17, 7.5), (21.5, 9), (21, 15.5)),
                   quad((21, 15.5), (20.5, 18.5), (18, 17)), quad((18, 17), (16.5, 15.5), (15, 14.5)),
                   line((9, 14.5)), quad((9, 14.5), (7.5, 15.5), (6, 17)),
                   quad((6, 17), (3.5, 18.5), (3, 15.5)), quad((3, 15.5), (2.5, 9), (7, 7.5))),
              line((8, 9.5), (8, 13)), line((6.3, 11.2), (9.7, 11.2)), circle(15.2, 10.2, 0.9), circle(17.6, 12.4, 0.9)],
  "chevron": [line((6, 9), (12, 15), (18, 9))],
  "check":   [line((5, 12.5), (10, 17.5), (19, 7))],
  "warn":    [line((12, 4), (21.5, 19.5), (2.5, 19.5), (12, 4)), line((12, 10), (12, 14)), line((12, 16.6), (12, 16.8))],
  "signal":  [arc(12, 18.5, 5, 225, 315, 6), arc(12, 18.5, 9.5, 225, 315, 8), arc(12, 18.5, 14, 225, 315, 10), line((12, 18.5), (12, 18.6))],
  "disc":    [circle(12, 12, 8.5), circle(12, 12, 2.4), arc(12, 12, 5.6, -60, 20, 6)],
  "chip":    [line((7, 7), (17, 7), (17, 17), (7, 17), (7, 7))] +
             [line((x, 3.5), (x, 7)) for x in (10, 14)] + [line((x, 17), (x, 20.5)) for x in (10, 14)] +
             [line((3.5, y), (7, y)) for y in (10, 14)] + [line((17, y), (20.5, y)) for y in (10, 14)],
  "monitor": [line((3, 5), (21, 5), (21, 16), (3, 16), (3, 5)), line((9, 20), (15, 20)), line((12, 16), (12, 20))],
  "play":    [line((8, 5), (19, 12), (8, 19), (8, 5))],
}

# ---- Lucide icons (ISC licence, design/lucide/LICENSE): read from their SVG files into the same strokes
import re
def _nums(s): return [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", s)]
def _cubic(p0, c1, c2, p1, n):
    return [[(1-t)**3*p0[0] + 3*(1-t)**2*t*c1[0] + 3*(1-t)*t*t*c2[0] + t**3*p1[0],
             (1-t)**3*p0[1] + 3*(1-t)**2*t*c1[1] + 3*(1-t)*t*t*c2[1] + t**3*p1[1]] for t in [i / n for i in range(1, n + 1)]]
def _steps(p0, p1, scale=1.0): return max(2, min(24, int(math.dist(p0, p1) * scale * 1.5) + 2))
def _arc(p0, rx, ry, phi, large, sweep, p1):
    """SVG elliptical arc to points (the SVG spec's endpoint-to-centre conversion)."""
    if rx == 0 or ry == 0 or p0 == p1: return [list(p1)]
    rx, ry, ph = abs(rx), abs(ry), math.radians(phi)
    c, s = math.cos(ph), math.sin(ph)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    lam = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if lam > 1: rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx*rx*ry*ry - rx*rx*y1*y1 - ry*ry*x1*x1
    k = math.sqrt(max(0.0, num / (rx*rx*y1*y1 + ry*ry*x1*x1))) * (-1 if large == sweep else 1)
    cx1, cy1 = k * rx * y1 / ry, -k * ry * x1 / rx
    cx, cy = c * cx1 - s * cy1 + (p0[0] + p1[0]) / 2, s * cx1 + c * cy1 + (p0[1] + p1[1]) / 2
    ang = lambda ux, uy, vx, vy: math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
    t1 = ang(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dt = ang((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dt > 0: dt -= 2 * math.pi
    if sweep and dt < 0: dt += 2 * math.pi
    n = max(2, int(abs(dt) * max(rx, ry) * 1.2) + 2)
    return [[cx + rx * math.cos(t1 + dt * i / n) * c - ry * math.sin(t1 + dt * i / n) * s,
             cy + rx * math.cos(t1 + dt * i / n) * s + ry * math.sin(t1 + dt * i / n) * c] for i in range(1, n + 1)]
def svg_path(d):
    """An SVG path's d attribute -> strokes (one per sub-path)."""
    toks = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", d)
    out, cur, pos, start, cmd, last_c, i = [], None, [0.0, 0.0], [0.0, 0.0], "", None, 0
    def take(n):
        nonlocal i
        v = [float(t) for t in toks[i:i + n]]; i += n; return v
    def flags():   # arc flags may be written together, like "0 0 1" or "011"
        nonlocal i
        t = toks[i]; i += 1; return float(t)
    while i < len(toks):
        if re.match(r"[A-Za-z]", toks[i]):
            cmd = toks[i]; i += 1
            if cmd in "Zz":
                if cur: cur.append(list(start))
                pos = list(start); cur = None; last_c = None; continue
        rel = cmd.islower(); C = cmd.upper()
        o = pos if rel else [0.0, 0.0]
        if C == "M":
            x, y = take(2); pos = [o[0] + x, o[1] + y]; start = list(pos); cur = [list(pos)]; out.append(cur)
            cmd = "l" if rel else "L"; last_c = None; continue
        if cur is None: cur = [list(pos)]; out.append(cur)
        if C == "L": x, y = take(2); pos = [o[0] + x, o[1] + y]; cur.append(list(pos)); last_c = None
        elif C == "H": x, = take(1); pos = [(pos[0] if rel else 0) + x, pos[1]]; cur.append(list(pos)); last_c = None
        elif C == "V": y, = take(1); pos = [pos[0], (pos[1] if rel else 0) + y]; cur.append(list(pos)); last_c = None
        elif C in "CS":
            if C == "C": a = take(6); c1 = [o[0] + a[0], o[1] + a[1]]; c2 = [o[0] + a[2], o[1] + a[3]]; p1 = [o[0] + a[4], o[1] + a[5]]
            else:
                a = take(4); c1 = [2 * pos[0] - last_c[0], 2 * pos[1] - last_c[1]] if last_c else list(pos)
                c2 = [o[0] + a[0], o[1] + a[1]]; p1 = [o[0] + a[2], o[1] + a[3]]
            cur += _cubic(pos, c1, c2, p1, _steps(pos, p1)); last_c = c2; pos = p1
        elif C in "QT":
            if C == "Q": a = take(4); c1 = [o[0] + a[0], o[1] + a[1]]; p1 = [o[0] + a[2], o[1] + a[3]]
            else: a = take(2); c1 = [2 * pos[0] - last_c[0], 2 * pos[1] - last_c[1]] if last_c else list(pos); p1 = [o[0] + a[0], o[1] + a[1]]
            cur += quad(pos, c1, p1, _steps(pos, p1))[1:]; last_c = c1; pos = p1
        elif C == "A":
            rx, ry, phi = take(3); large, sweep = flags(), flags(); x, y = take(2); p1 = [o[0] + x, o[1] + y]
            cur += _arc(pos, rx, ry, phi, bool(large), bool(sweep), p1); pos = p1; last_c = None
    return [s for s in out if len(s) > 1]
def lucide(name):
    """design/lucide/<name>.svg -> (strokes, element index of each stroke)."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lucide", name + ".svg")).read()
    strokes, owner = [], []
    for el_i, m in enumerate(re.finditer(r"<(path|line|polyline|polygon|circle|rect|ellipse)\b([^>]*)/?>", src)):
        tag, a = m.group(1), dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(2)))
        f = lambda k, d=0.0: float(a.get(k, d))
        if tag == "path": got = svg_path(a["d"])
        elif tag == "line": got = [[[f("x1"), f("y1")], [f("x2"), f("y2")]]]
        elif tag in ("polyline", "polygon"):
            v = _nums(a["points"]); pts = [[v[k], v[k + 1]] for k in range(0, len(v) - 1, 2)]
            got = [pts + ([pts[0]] if tag == "polygon" else [])]
        elif tag in ("circle", "ellipse"):
            rx = f("r") if tag == "circle" else f("rx"); ry = f("r") if tag == "circle" else f("ry")
            n = max(12, int(max(rx, ry) * 6))
            got = [[[f("cx") + rx * math.cos(2 * math.pi * k / n), f("cy") + ry * math.sin(2 * math.pi * k / n)] for k in range(n + 1)]]
        else:
            x, y, w, h = f("x"), f("y"), f("width"), f("height"); rr = min(f("rx", a.get("ry", 0)), w / 2, h / 2)
            d = (f"M{x + rr} {y}H{x + w - rr}" + (f"A{rr} {rr} 0 0 1 {x + w} {y + rr}" if rr else "") + f"V{y + h - rr}" +
                 (f"A{rr} {rr} 0 0 1 {x + w - rr} {y + h}" if rr else "") + f"H{x + rr}" +
                 (f"A{rr} {rr} 0 0 1 {x} {y + h - rr}" if rr else "") + f"V{y + rr}" + (f"A{rr} {rr} 0 0 1 {x + rr} {y}" if rr else "") + "Z")
            got = svg_path(d)
        if tag in ("line", "path") and all(math.dist(s[0], s[-1]) < 0.05 and len(s) <= 2 for s in got):
            got = [[s[0], [s[0][0] + 0.01, s[0][1]]] for s in got]      # Lucide's dots ("h.01")
        strokes += got; owner += [el_i] * len(got)
    return strokes, owner

# app name -> Lucide file
LUCIDE = {"star": "star", "calendar": "calendar", "users": "users", "tag": "tag", "building": "building",
          "gamepad": "gamepad-2", "go": "play", "seek": "search", "clock": "clock", "sparkles": "sparkles",
          "download": "download", "image": "image", "film": "film", "sound": "volume-2", "mute": "volume-x", "exit": "x"}
# Motions ported from lucide-animated (MIT, design/lucide/LICENSE-lucide-animated): which SVG elements move,
# about which point, and keyframes [time 0-1, dx, dy, rotate degrees, opacity]; dur in seconds.
MOTIONS = {
    "users":    {"dur": 0.6, "parts": [{"el": [1, 2], "origin": [12, 12], "keys": [[0, -6, 0, 0, 1], [0.55, 0.8, 0, 0, 1], [0.8, -0.3, 0, 0, 1], [1, 0, 0, 0, 1]]}]},
    "go":       {"dur": 0.5, "parts": [{"el": [0], "origin": [12, 12], "keys": [[0, 0, 0, 0, 1], [0.2, -1, 0, -10, 1], [0.5, 2, 0, 0, 1], [1, 0, 0, 0, 1]]}]},
    "seek":     {"dur": 0.8, "parts": [{"el": [0, 1], "origin": [12, 12], "keys": [[0, 0, 0, 0, 1], [0.33, 0, -4, 0, 1], [0.66, -3, 0, 0, 1], [1, 0, 0, 0, 1]]}]},
    "download": {"dur": 1.0, "parts": [{"el": [0, 2], "origin": [12, 12], "keys": [[0, 0, 0, 0, 1], [0.3, 0, 2.6, 0, 1], [0.45, 0, 1.8, 0, 1], [0.6, 0, 2, 0, 1], [1, 0, 0, 0, 1]]}]},
    "clock":    {"dur": 1.0, "parts": [{"el": [1], "origin": [12, 12], "keys": [[0, 0, 0, 0, 1], [1, 0, 0, 360, 1]]}]},
    "sparkles": {"dur": 2.0, "parts": [{"el": [0], "origin": [12, 12], "keys": [[0, 0, 0, 0, 1], [0.17, 0, -1, 0, 1], [0.33, 0, 0, 0, 1], [1, 0, 0, 0, 1]]},
                                       {"el": [1, 2, 3], "origin": [12, 12], "keys": [[0, 0, 0, 0, 0], [0.17, 0, 0, 0, 1], [0.33, 0, 0, 0, 0], [0.83, 0, 0, 0, 0], [1, 0, 0, 0, 1]]}]},
    "sound":    {"dur": 0.45, "parts": [{"el": [1], "origin": [12, 12], "keys": [[0, 0, 0, 0, 0], [0.22, 0, 0, 0, 0], [0.55, 0, 0, 0, 1], [1, 0, 0, 0, 1]]},
                                        {"el": [2], "origin": [12, 12], "keys": [[0, 0, 0, 0, 0], [0.44, 0, 0, 0, 0], [0.8, 0, 0, 0, 1], [1, 0, 0, 0, 1]]}]},
}
motions = {}
for key, fname in LUCIDE.items():
    assert key not in ICONS, key
    strokes, owner = lucide(fname)
    ICONS[key] = strokes
    if key in MOTIONS:
        mo = MOTIONS[key]
        motions[key] = {"dur": mo["dur"], "parts": [{"strokes": [i for i, e in enumerate(owner) if e in p["el"]], "origin": p["origin"],
                                                     "keys": p["keys"]} for p in mo["parts"]]}

def r(p): return [[round(x, 2), round(y, 2)] for x, y in p]
data = {k: [r(s) for s in v] for k, v in ICONS.items()}

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
with open(os.path.join(root, "design", "icons.json"), "w") as f:
    json.dump(data, f, separators=(",", ":"))
with open(os.path.join(root, "desktop", "icons.py"), "w") as f:
    f.write('"""Generated by design/make_icons.py. Do not edit. Strokes on a 24x24 grid.\n'
            'Some icons are Lucide icons (ISC licence) and their MOTIONS are ported from lucide-animated (MIT): see NOTICE.md."""\n'
            'ICONS = ' + repr(data) + "\nMOTIONS = " + repr(motions) + "\n")

def jf(p): return "{" + ",".join(f"{x:g}f,{y:g}f" for x, y in p) + "}"
java = ["package net.netplay.app;", "", "/**", " * Generated by design/make_icons.py. Do not edit. Strokes on a 24x24 grid as x,y pairs.",
        " * Some icons are Lucide icons (ISC licence) and their motions are ported from lucide-animated (MIT): see NOTICE.md.",
        " * A motion is {seconds, parts, then per part: strokes, stroke indexes..., originX, originY, keys,",
        " * and per key: time 0-1, dx, dy, rotate degrees, opacity}.", " */",
        "final class Icons {", "    private Icons() { }"]
for k, strokes in data.items():
    java.append(f"    static final float[][] {k.upper()} = {{ " + ", ".join(jf(s) for s in strokes) + " };")
for k, mo in motions.items():
    flat = [mo["dur"], len(mo["parts"])]
    for part in mo["parts"]:
        flat += [len(part["strokes"])] + part["strokes"] + part["origin"] + [len(part["keys"])] + [v for key in part["keys"] for v in key]
    java.append(f"    static final float[] MOTION_{k.upper()} = {{" + ",".join(f"{v:g}f" for v in flat) + "};")
java.append("    /** The motion for an icon, or null. */")
java.append("    static float[] motion(float[][] icon) {")
for k in motions:
    java.append(f"        if (icon == {k.upper()}) return MOTION_{k.upper()};")
java.append("        return null;")
java.append("    }")
java.append("}")
p = os.path.join(root, "android", "app", "src", "main", "java", "net", "netplay", "app", "Icons.java")
open(p, "w").write("\n".join(java) + "\n")
print("icons:", len(data), "->", p)
