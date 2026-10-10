"""Ink-and-paper look for the desktop app: theme, stippled hero picture, heat squares, animated stroke icons."""
import math
import random

from icons import ICONS

PAPER, PAPER_DEEP, INK, SOFT = "#e4e3df", "#d8d7d2", "#141414", "#4a4a48"
STOPS = [(127, 208, 255), (102, 242, 176), (232, 240, 74), (255, 147, 49), (210, 54, 46), (122, 43, 143)]
READY, UNKNOWN, ATTENTION = 1, 0, 2
BASE = {UNKNOWN: 0.04, READY: 0.30, ATTENTION: 0.86}


def spectrum(t):
    t = max(0.0, min(1.0, t)) * (len(STOPS) - 1)
    i = min(int(t), len(STOPS) - 2)
    f = t - i
    a, b = STOPS[i], STOPS[i + 1]
    return "#%02x%02x%02x" % tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def mono_family(root):
    import tkinter.font as tkfont
    have = set(tkfont.families(root))
    for name in ("DejaVu Sans Mono", "Menlo", "Consolas", "Liberation Mono", "Cascadia Mono", "Courier New"):
        if name in have:
            return name
    return "TkFixedFont"


def apply_theme(root):
    """Paper background, ink text, flat hairline buttons, mono type, for every window of the app."""
    from tkinter import ttk
    fam = mono_family(root)
    root.configure(bg=PAPER)
    root.option_add("*Background", PAPER)
    root.option_add("*Foreground", INK)
    root.option_add("*Font", (fam, 10))
    root.option_add("*Label.Background", PAPER)
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except Exception:
        pass
    st.configure(".", background=PAPER, foreground=INK, font=(fam, 10), bordercolor=INK, lightcolor=PAPER, darkcolor=PAPER,
                 focuscolor=INK, troughcolor=PAPER_DEEP)
    st.configure("TFrame", background=PAPER)
    st.configure("TLabel", background=PAPER, foreground=INK)
    st.configure("TButton", background=PAPER, foreground=INK, borderwidth=1, relief="solid", padding=(12, 7), font=(fam, 10, "bold"))
    st.map("TButton", background=[("active", PAPER_DEEP), ("pressed", INK)], foreground=[("pressed", PAPER)],
           bordercolor=[("focus", INK)])
    st.configure("TEntry", fieldbackground="#efeeea", foreground=INK, bordercolor=INK, padding=6)
    st.configure("TCombobox", fieldbackground="#efeeea", background=PAPER, foreground=INK, bordercolor=INK, arrowcolor=INK, padding=6)
    st.map("TCombobox", fieldbackground=[("readonly", "#efeeea")], selectbackground=[("readonly", "#efeeea")], selectforeground=[("readonly", INK)])
    st.configure("TCheckbutton", background=PAPER, foreground=INK, indicatorcolor="#efeeea")
    st.configure("TRadiobutton", background=PAPER, foreground=INK, indicatorcolor="#efeeea")
    st.map("TCheckbutton", indicatorcolor=[("selected", INK)])
    st.map("TRadiobutton", indicatorcolor=[("selected", INK)])
    return fam


# ------------------------------------------------------------------ heat squares
class Heat:
    """A dithered gradient square drawn into a PhotoImage. Cool = unknown, green = ready, orange to purple = attention."""
    N = 22

    def __init__(self, tk, zoom=4, seed=1):
        self.tk, self.zoom = tk, zoom
        r = random.Random(seed)
        self.noise = [r.random() for _ in range(self.N * self.N)]
        self.small = tk.PhotoImage(width=self.N, height=self.N)
        self.image = self.small.zoom(zoom)
        self.lut = [spectrum(i / 255) for i in range(256)]

    def paint(self, state, clock=0.0, index=0, searching=False):
        N = self.N
        base = BASE[state] + (0.16 * math.sin(clock * 2.2 + index * 1.7) if searching else 0)
        ang = clock * 1.1 if searching else 0.6
        ca, sa = math.cos(ang), math.sin(ang)
        rows = []
        for y in range(N):
            row = []
            for x in range(N):
                g = ca * (x / N - 0.5) + sa * (y / N - 0.5)
                t = base + 0.55 * g + (self.noise[y * N + x] - 0.5) * 0.34
                row.append(self.lut[max(0, min(255, int(t * 255)))])
            rows.append("{" + " ".join(row) + "}")
        self.small.put(" ".join(rows))
        self.image = self.small.zoom(self.zoom)
        return self.image


# ------------------------------------------------------------------ stippled controller
def draw_hero(canvas, w, h, family):
    """Controller as ink grain on paper, thinning toward the lower left, with the corner captions."""
    canvas.delete("art")
    rnd = random.Random(5)
    body = ICONS["pad"][0]
    ang = math.radians(-9)
    ca, sa = math.cos(ang), math.sin(ang)
    s = w * 0.0345

    def tf(p):
        x, y = (p[0] - 12) * s, (p[1] - 12) * s
        return (x * ca - y * sa + w * 0.54, x * sa + y * ca + h * 0.52)

    poly = [tf(p) for p in body]
    holes = []
    for st in ICONS["pad"][3:]:
        cx = sum(p[0] for p in st) / len(st)
        cy = sum(p[1] for p in st) / len(st)
        holes.append((tf((cx, cy)), s * 1.1))
    cuts = [[tf(p) for p in st] for st in ICONS["pad"][1:3]]
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    x0, x1, y0, y1 = min(xs) - 24, max(xs) + 24, min(ys) - 24, max(ys) + 24

    def inside(x, y):
        c = False
        j = len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-9) + xi:
                c = not c
            j = i
        return c

    def hole(x, y):
        for (hx, hy), r in holes:
            if (x - hx) ** 2 + (y - hy) ** 2 < r * r:
                return True
        for seg in cuts:
            for (ax, ay), (bx, by) in zip(seg, seg[1:]):
                dx, dy = bx - ax, by - ay
                t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy + 1e-9)))
                if (x - ax - t * dx) ** 2 + (y - ay - t * dy) ** 2 < (s * 0.45) ** 2:
                    return True
        return False

    # distance falloff keeps the edge breaking up into grain
    for _ in range(12000):
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        if hole(x, y):
            continue
        inn = inside(x, y)
        fade = max(0.0, min(1.0, 1.5 * (0.62 * x / w + 0.55 * (1 - y / h)) - 0.35))
        if inn:
            p = 0.25 + 0.75 * fade
        else:
            # outside: a thin halo that rapidly thins
            dmin = min(math.hypot(x - q[0], y - q[1]) for q in poly[::3])
            p = max(0.0, 1 - dmin / 16) * (0.25 + 0.75 * fade) * 0.9
        if rnd.random() > p * 1.4:
            continue
        sz = 1 + (1 if rnd.random() < p * 0.5 else 0)
        shade = int(20 + (1 - min(1, p + 0.3 * rnd.random())) ** 1.5 * 170)
        canvas.create_rectangle(x, y, x + sz, y + sz, outline="", fill="#%02x%02x%02x" % (shade, shade, shade), tags="art")
    pad = 18
    canvas.create_text(pad, pad, anchor="nw", text="PLAY TOGETHER,\nFROM ANYWHERE.", font=(family, 11), fill=INK, tags="art")
    canvas.create_text(w - pad, h - 12, anchor="se", text="RETRO × NETPLAY", font=(family, 11), fill=INK, tags="art")


# ------------------------------------------------------------------ animated stroke icon button
class InkButton:
    """Solid ink button whose icon draws itself on when pressed and loops while work is in progress."""

    def __init__(self, tk, parent, text, icon, command, family, height=64):
        self.tk, self.family, self.command, self.icon = tk, family, command, icon
        self.c = tk.Canvas(parent, height=height, bg=INK, highlightthickness=2, highlightbackground=INK, highlightcolor="#66f2b0", takefocus=1, cursor="hand2")
        self.text, self.progress, self.erase, self.job, self.loop = text, 1.0, 0.0, None, False
        self.p = self.t0 = 1.0
        self.c.bind("<Configure>", lambda e: self.draw())
        self.c.bind("<Button-1>", lambda e: self.press())
        self.c.bind("<Return>", lambda e: self.press())
        self.c.bind("<space>", lambda e: self.press())

    def pack(self, **kw):
        self.c.pack(**kw)

    def press(self):
        self.play()
        self.command()

    def set(self, text, icon, loop=False):
        self.text, self.icon, self.loop = text, icon, loop
        self.play()

    def play(self):
        self.erase, self.p, self.t0 = 0.0, 0.0, 0.0
        self._step()

    def _step(self):
        if self.job:
            self.c.after_cancel(self.job)
            self.job = None
        if self.loop:
            self.t0 = (self.t0 + 0.035) % 2.0
            self.progress, self.erase = (self.t0, 0.0) if self.t0 < 1 else (1.0, self.t0 - 1)
            self.job = self.c.after(33, self._step)
        else:
            self.erase = 0.0
            self.p = min(1.0, self.p + 0.07)
            self.progress = 1 - (1 - self.p) ** 2
            if self.p < 1.0:
                self.job = self.c.after(16, self._step)
        self.draw()

    def draw(self):
        c = self.c
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        size = 26
        gx = 22
        strokes = ICONS[self.icon]
        n = len(strokes)
        k = size / 24
        oy = (h - size) / 2
        for i, st in enumerate(strokes):
            start = 0.5 * i / (n - 1) if n > 1 else 0.0
            lp = max(0.0, min(1.0, (self.progress - start) / (0.5 if n > 1 else 1.0)))
            if lp <= 0:
                continue
            pts = [(gx + x * k, oy + y * k) for x, y in st]
            seglen = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
            total = sum(seglen) or 1
            lo, hi = total * self.erase * lp, total * lp
            out, acc = [], 0.0
            for (a, b), L in zip(zip(pts, pts[1:]), seglen):
                s0, s1 = acc, acc + L
                acc = s1
                if s1 <= lo or s0 >= hi or L == 0:
                    continue
                fa, fb = max(0.0, (lo - s0) / L), min(1.0, (hi - s0) / L)
                out += [(a[0] + (b[0] - a[0]) * fa, a[1] + (b[1] - a[1]) * fa), (a[0] + (b[0] - a[0]) * fb, a[1] + (b[1] - a[1]) * fb)]
            if len(out) >= 2:
                c.create_line(*[v for pt in out for v in pt], fill=PAPER, width=2.4, capstyle="round", joinstyle="round")
        c.create_text(gx + size + 14, h / 2, anchor="w", text=self.text.upper(), font=(self.family, 13, "bold"), fill=PAPER)
