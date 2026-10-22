"""A game's title card: a moving background themed on the game (its box art's colours, or its system's), the box art,
the details from its gamelist, a gameplay window (its screenshots, slowly panning and fading on an old-TV screen)
and Play. It stays open while the app looks for an opponent.

The background is a port of two effects from Shaders (https://github.com/shader-effects-inc/shaders, MIT licence):
MeshGradient (drifting colour points blended by nearness, with a gentle swirl) and CRTScreen (scanlines and a
vignette). Shaders runs on the GPU in web pages; this is the same maths drawn small with Pillow and scaled up.
Needs Pillow (and its Tk part); without it the card shows the details on a plain background.
"""
import math
import random
import textwrap
import time

from icons import ICONS, MOTIONS

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
except ImportError:          # running from source without Pillow
    Image = None
try:
    from PIL import ImageTk
except ImportError:          # Pillow without its Tk part (Linux: python3-pil.imagetk)
    ImageTk = None
PICTURES = Image is not None and ImageTk is not None

W, H = 960, 540
THEMES = {   # dark -> light; the card's colours when a game has no box art
    "gba": ["#120a2e", "#3a1d7a", "#7b3fd0", "#d45ab8", "#ffd2f0"],
    "ps1": ["#0b0f1a", "#1c2a44", "#3d5a80", "#98c1d9", "#e0fbfc"],
    "ps2": ["#02040f", "#061a40", "#0353a4", "#00a6fb", "#c6f1ff"],
    "ps3": ["#050505", "#1b1b22", "#3a3d4a", "#8a8fa3", "#e6e8ee"],
    "dreamcast": ["#1a0700", "#5a1c00", "#c2410c", "#fb923c", "#fff1e0"],
    "gamecube": ["#0d0a24", "#2a1f6b", "#5b4bc4", "#a99cf5", "#efeaff"],
}
TEXT, SOFT, ACCENT = "#f7f5ef", "#c9c6bd", "#66f2b0"
SHOW, FADE = 5.0, 0.8          # seconds per screenshot, crossfade


def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _hash(x):
    return (math.sin(x * 12.9898 + 78.233) * 43758.5453) % 1.0


def palette_from(img, fallback):
    """Five colours from the box art, dark to light, darkened enough for light text on top."""
    try:
        small = img.convert("RGB").resize((48, 48))
        q = small.quantize(colors=6, method=Image.Quantize.MEDIANCUT)
        pal = q.getpalette()[:18]
        counts = sorted(q.getcolors(), reverse=True)
        cols = [tuple(pal[i * 3:i * 3 + 3]) for _, i in counts][:5]
    except Exception:
        return [_rgb(c) for c in fallback]
    if len(cols) < 3:
        return [_rgb(c) for c in fallback]
    lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    cols.sort(key=lum)
    while len(cols) < 5:
        cols.append(cols[-1])
    dark = [0.22, 0.38, 0.6, 0.8, 0.95]
    return [tuple(int(v * d) for v in c) for c, d in zip(cols, dark)]


class Mesh:
    """Shaders' MeshGradient: `count` colour points scattered on a golden-angle spiral, drifting on slow Lissajous
    paths; each pixel blends the points' values by inverse distance (power from `smoothness`), after a swirl about
    the centre; the value picks a colour from the palette ramp."""

    def __init__(self, stops, count=5, smoothness=2.0, swirl=0.3, drift=0.5, seed=3.0, lw=64, lh=36):
        self.stops, self.count, self.drift, self.seed = stops, count, drift, seed
        self.power = 8.0 / (smoothness + 0.5)
        self.swirl, self.lw, self.lh = swirl, lw, lh
        self.aspect = lw / lh
        self.ti = [(i * 0.6180339887 + _hash(seed * 0.531 + 0.77)) % 1.0 for i in range(count)]
        self.ramp = [self._ramp(k / 255) for k in range(256)]
        # the swirl only depends on the pixel: rotate once, keep the swirled coordinates
        self.grid = []
        for y in range(lh):
            for x in range(lw):
                cx, cy = (x + 0.5) / lw * self.aspect - self.aspect / 2, (y + 0.5) / lh - 0.5
                r = min(1.0, math.hypot(cx, cy))
                a = -3.0 * swirl * (r * r * (3 - 2 * r))
                ca, sa = math.cos(a), math.sin(a)
                self.grid.append((cx * ca - cy * sa + self.aspect / 2, cx * sa + cy * ca + 0.5))

    def _ramp(self, t):
        n = len(self.stops) - 1
        i = min(int(t * n), n - 1)
        f = t * n - i
        a, b = self.stops[i], self.stops[i + 1]
        return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))

    def anchors(self, t):
        out = []
        for i in range(self.count):
            theta = i * 2.39996322972865 + _hash(self.seed * 0.7311 + 1.618) * math.tau
            r = math.sqrt((i + 0.5) / self.count)
            jx, jy = (_hash(i * 3.77 + 0.917) - 0.5) * 0.26, (_hash(self.seed * 1.093 + 2.236 + i) - 0.5) * 0.26
            px, py = _hash(i * 7.13 + 1.618) * math.tau, _hash(self.seed * 0.531 + 3.71 + i) * math.tau
            w1 = 0.5 + _hash(i * 5.417 + self.seed * 0.291 + 0.917) * 0.7
            dx, dy = math.sin(t * 0.11 * w1 + px), math.cos(t * 0.11 * w1 * 1.37 + py)
            nx = math.cos(theta) * r * 0.62 + jx + dx * self.drift * 0.16
            ny = math.sin(theta) * r * 0.62 + jy + dy * self.drift * 0.16
            out.append(((0.5 + nx) * self.aspect, 0.5 + ny))
        return out

    def render(self, t):
        pts, ti, half = self.anchors(t), self.ti, self.power / 2
        ramp, data = self.ramp, []
        for qx, qy in self.grid:
            num = den = 0.0
            for (ax, ay), v in zip(pts, ti):
                d2 = max((qx - ax) ** 2 + (qy - ay) ** 2, 1e-4)
                w = d2 ** -half
                num += w * v
                den += w
            data.append(ramp[int(num / den * 255)])
        img = Image.new("RGB", (self.lw, self.lh))
        img.putdata(data)
        return img


def crt_mask(w, h, lines, intensity, vignette, scrim=False):
    """Shaders' CRTScreen scanlines and vignette as one brightness mask (multiply the picture by it)."""
    col = Image.new("L", (1, h))
    for y in range(h):
        v = 1.0 - intensity * (math.sin((y + 0.5) / h * lines * math.tau) * 0.5 + 0.5)
        if scrim:            # darker at the top and bottom, where the text sits
            fy = y / h
            v *= 1.0 - 0.45 * max(0.0, 1 - fy / 0.28) ** 1.5 - 0.6 * max(0.0, (fy - 0.62) / 0.38) ** 1.4
        col.putpixel((0, y), int(max(0.0, v) * 255))
    lines_img = col.resize((w, h))
    vig = Image.new("L", (64, 36))
    aspect = w / h
    for y in range(36):
        for x in range(64):
            d = math.hypot(((x + 0.5) / 64 - 0.5) * aspect, (y + 0.5) / 36 - 0.5)
            inner = 1.0 - 0.5 * 0.9
            s = min(1.0, max(0.0, (d - inner) / 0.5))
            vig.putpixel((x, y), int((1.0 - vignette * s * s * (3 - 2 * s)) * 255))
    return ImageChops.multiply(lines_img, vig.resize((w, h), Image.BILINEAR)).convert("RGB")


def rounded(size, r):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], r, fill=255)
    return m


def load(path, cache={}):
    if path not in cache:
        try:
            im = Image.open(path)
            im.load()
            cache[path] = im.convert("RGB")
        except Exception:
            cache[path] = None
    return cache[path]


def cover(img, w, h, pixel=False):
    """Scale img to cover w x h (pixel art: whole-number steps first, so it stays crisp)."""
    iw, ih = img.size
    if pixel and iw < w and ih < h:
        k = max(1, int(max(w / iw, h / ih)))
        img = img.resize((iw * k, ih * k), Image.NEAREST)
        iw, ih = img.size
    s = max(w / iw, h / ih)
    return img.resize((max(w, int(iw * s + 0.5)), max(h, int(ih * s + 0.5))), Image.LANCZOS)


def fit(img, w, h):
    iw, ih = img.size
    s = min(w / iw, h / ih)
    return img.resize((max(1, int(iw * s)), max(1, int(ih * s))), Image.LANCZOS)


class Slides:
    """The gameplay window: screenshots slowly zooming and panning (Ken Burns), crossfading, on an old TV."""

    def __init__(self, paths, w, h):
        self.w, self.h = w, h
        self.imgs = []
        for p in paths:
            im = load(p)
            if im is not None:
                self.imgs.append(cover(im, int(w * 1.12), int(h * 1.12), pixel=im.size[0] <= 640))
        self.mask = crt_mask(w, h, h / 3.0, 0.22, 0.55)
        self.round = rounded((w, h), 14)
        rnd = random.Random(7)
        self.moves = [(rnd.choice((-1, 1)), rnd.choice((-1, 1))) for _ in self.imgs]

    def frame(self, t):
        if not self.imgs:
            return None
        n = len(self.imgs)
        i, local = int(t // SHOW) % n, t % SHOW
        img = self._shot(i, local)
        if n > 1 and local > SHOW - FADE:
            img = Image.blend(img, self._shot((i + 1) % n, local - SHOW), (local - (SHOW - FADE)) / FADE)
        return ImageChops.multiply(img, self.mask)

    def _shot(self, i, local):
        src = self.imgs[i]
        p = max(0.0, min(1.0, (local + FADE) / (SHOW + FADE)))
        z = 1.10 - 0.08 * p
        cw, ch = int(self.w * z), int(self.h * z)
        sx, sy = self.moves[i]
        x = int((src.width - cw) * (0.5 + 0.45 * sx * (p - 0.5)))
        y = int((src.height - ch) * (0.5 + 0.45 * sy * (p - 0.5)))
        x, y = max(0, min(src.width - cw, x)), max(0, min(src.height - ch, y))
        return src.crop((x, y, x + cw, y + ch)).resize((self.w, self.h), Image.BILINEAR)


class Icon:
    """A stroke icon on a canvas that can play its motion (ported from lucide-animated)."""

    def __init__(self, canvas, key, x, y, size, color, width=2.0):
        self.c, self.key, self.x, self.y, self.k = canvas, key, x, y, size / 24
        self.color, self.width, self.t0, self.items = color, width, None, []
        self.draw(0.0)

    def draw(self, t):
        for it in self.items:
            self.c.delete(it)
        self.items = []
        mo = MOTIONS.get(self.key)
        tf = {}
        if mo and t > 0:
            u = min(1.0, t / mo["dur"])
            for part in mo["parts"]:
                v = _keys(part["keys"], u)
                for s in part["strokes"]:
                    tf[s] = (v, part["origin"])
        for i, st in enumerate(ICONS[self.key]):
            pts = st
            if i in tf:
                (dx, dy, rot, alpha), (ox, oy) = tf[i]
                if alpha < 0.5:
                    continue
                cr, sr = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                pts = [(ox + (px - ox) * cr - (py - oy) * sr + dx, oy + (px - ox) * sr + (py - oy) * cr + dy) for px, py in st]
            flat = [v for px, py in pts for v in (self.x + px * self.k, self.y + py * self.k)]
            self.items.append(self.c.create_line(*flat, fill=self.color, width=self.width, capstyle="round", joinstyle="round"))

    def play(self):
        if self.key not in MOTIONS:
            return
        self.t0 = time.monotonic()
        self._step()

    def _step(self):
        if self.t0 is None:
            return
        t = time.monotonic() - self.t0
        self.draw(t)
        if t < MOTIONS[self.key]["dur"]:
            self.c.after(16, self._step)
        else:
            self.t0 = None
            self.draw(0.0)


def _keys(keys, u):
    for a, b in zip(keys, keys[1:]):
        if u <= b[0]:
            f = 0.0 if b[0] == a[0] else (u - a[0]) / (b[0] - a[0])
            f = f * f * (3 - 2 * f)
            return tuple(a[k] + (b[k] - a[k]) * f for k in range(1, 5))
    return tuple(keys[-1][1:5])


class Card:
    """One title card window. on_play(): Play (or Leave queue while searching); sound(name): play a UI sound."""

    def __init__(self, root, tk, family, game, info, system_label, on_play, sound=lambda name: None, on_close=None,
                 scale=None):
        self.tk, self.fam, self.game, self.info, self.on_play, self.sound = tk, family, game, info, on_play, sound
        self.on_close = on_close
        self.win = tk.Toplevel(root)
        self.win.title(info.get("name") or game.get("name", "Game"))
        self.win.configure(bg="#000000")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.s = scale or max(0.6, min(1.0, sw * 0.9 / W, sh * 0.85 / H))
        self.W, self.H = int(W * self.s), int(H * self.s)
        self.win.geometry(f"{self.W}x{self.H}")
        self.win.resizable(False, False)
        self.c = tk.Canvas(self.win, width=self.W, height=self.H, highlightthickness=0, bg="#000000", bd=0)
        self.c.pack()
        self.state, self.alive, self.focused, self.t_start = "idle", True, True, time.monotonic()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<Return>", lambda e: self.press())
        self.win.bind("<FocusIn>", lambda e: self._focus(True))
        self.win.bind("<FocusOut>", lambda e: self._focus(False))
        stops = [_rgb(x) for x in THEMES.get(game.get("system"), THEMES["ps2"])]
        self.photo = None
        if PICTURES:
            box = load(info["box"]) if info.get("box") else None
            if box is not None:
                stops = palette_from(box, THEMES.get(game.get("system"), THEMES["ps2"]))
            self._build_images(box, stops)
        else:
            self.c.configure(bg="#%02x%02x%02x" % stops[1])
        self._build_items(system_label)
        self.sound("open")
        self._tick()

    # ---- pictures (Pillow)
    def _build_images(self, box, stops):
        s, Wd, Ht = self.s, self.W, self.H
        self.mesh = Mesh(stops, seed=3.0 + (sum(map(ord, self.game.get("name", ""))) % 17))
        self.shade = crt_mask(Wd, Ht, Ht / 3.0, 0.10, 0.7, scrim=True)
        back = None
        for p in [self.info.get("fanart")] + list(self.info.get("screens") or []):
            if p and load(p) is not None:
                back = load(p)
                break
        self.backdrop = cover(back, Wd, Ht).crop((0, 0, Wd, Ht)).filter(ImageFilter.GaussianBlur(18 * s)) if back else None
        # the box art with a soft shadow, and the gameplay window's frame, drawn once
        self.fg = Image.new("RGBA", (Wd, Ht), (0, 0, 0, 0))
        bx, by, bw, bh = int(40 * s), int(112 * s), int(230 * s), int(316 * s)
        self.box_rect = (bx, by, bw, bh)
        if box is not None:
            art = fit(box, bw, bh)
            ax, ay = bx + (bw - art.width) // 2, by + (bh - art.height) // 2
            sh = Image.new("RGBA", (Wd, Ht), (0, 0, 0, 0))
            ImageDraw.Draw(sh).rounded_rectangle([ax + 6 * s, ay + 10 * s, ax + art.width + 6 * s, ay + art.height + 10 * s],
                                                 int(8 * s), fill=(0, 0, 0, 170))
            self.fg = Image.alpha_composite(self.fg, sh.filter(ImageFilter.GaussianBlur(10 * s)))
            self.fg.paste(art, (ax, ay), rounded(art.size, int(6 * s)))
            self.has_box = True
        else:
            ImageDraw.Draw(self.fg).rounded_rectangle([bx, by, bx + bw, by + bh], int(10 * s), outline=(255, 255, 255, 90),
                                                      width=max(1, int(2 * s)), fill=(0, 0, 0, 70))
            self.has_box = False
        gx, gy, gw, gh = int(300 * s), int(112 * s), int(620 * s), int(316 * s)
        self.game_rect = (gx, gy, gw, gh)
        shots = list(self.info.get("screens") or []) or ([self.info["title"]] if self.info.get("title") else [])
        self.slides = Slides(shots, gw, gh)
        glow = Image.new("RGBA", (Wd, Ht), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle([gx - 6 * s, gy - 6 * s, gx + gw + 6 * s, gy + gh + 6 * s], int(18 * s),
                                               fill=stops[3] + (110,))
        self.fg = Image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(14 * s)), self.fg)
        ImageDraw.Draw(self.fg).rounded_rectangle([gx - 2, gy - 2, gx + gw + 1, gy + gh + 1], int(15 * s), outline=(255, 255, 255, 60), width=2)
        if not self.slides.imgs:
            ImageDraw.Draw(self.fg).rounded_rectangle([gx, gy, gx + gw, gy + gh], int(14 * s), fill=(0, 0, 0, 120))
        self.photo = ImageTk.PhotoImage(Image.new("RGB", (Wd, Ht)))
        self.c.create_image(0, 0, anchor="nw", image=self.photo, tags="bg")

    def _frame(self, t):
        grad = self.mesh.render(t * 2.2).resize((self.W, self.H), Image.BICUBIC)
        if self.backdrop is not None:
            grad = Image.blend(grad, self.backdrop, 0.42)
        frame = ImageChops.multiply(grad, self.shade)
        shot = self.slides.frame(t)
        if shot is not None:
            gx, gy, gw, gh = self.game_rect
            frame.paste(shot, (gx, gy), self.slides.round)
        frame.paste(self.fg, (0, 0), self.fg)
        return frame

    # ---- text, icons and buttons (canvas items, crisp at any size)
    def _text(self, x, y, text, size, color=TEXT, bold=False, anchor="nw", width=None, tag=None):
        f = (self.fam, int(size * self.s), "bold" if bold else "normal")
        kw = {"anchor": anchor, "text": text, "font": f}
        if width:
            kw["width"] = int(width * self.s)
        self.c.create_text(x * self.s + 2, y * self.s + 2, fill="#000000", tags=("shadow",) + ((tag,) if tag else ()), **kw)
        return self.c.create_text(x * self.s, y * self.s, fill=color, tags=(tag,) if tag else (), **kw)

    def _build_items(self, system_label):
        info, s = self.info, self.s
        name = info.get("name") or self.game.get("name", "")
        self._text(40, 26, name if len(name) <= 48 else name[:46] + "...", 22, bold=True)
        meta = [("gamepad", system_label)]
        if info.get("year"):
            meta.append(("calendar", info["year"]))
        who = info.get("developer") or info.get("publisher")
        if who:
            meta.append(("building", who if len(who) <= 26 else who[:24] + "..."))
        if info.get("players"):
            meta.append(("users", info["players"] + (" player" if info["players"] == "1" else " players")))
        if info.get("genre"):
            g = info["genre"]
            meta.append(("tag", g if len(g) <= 24 else g[:22] + "..."))
        x = 40
        self.icons = []
        for key, label in meta:
            ic = Icon(self.c, key, x * s, 72 * s, 18 * s, ACCENT, max(1.2, 1.8 * s))
            self.icons.append(ic)
            self._text(x + 24, 72, label, 11, SOFT)
            x += 24 + len(label) * 8.6 + 22
        for ic in self.icons:
            self.win.after(250 + 120 * self.icons.index(ic), ic.play)
        if info.get("rating"):
            stars = round(float(info["rating"]) * 5 * 2) / 2
            for i in range(5):
                fill = ACCENT if i + 0.5 < stars else ("#7a8a80" if i < stars else "")
                pts = [v for px, py in ICONS["star"][0] for v in ((40 + i * 22) * s + px * 0.75 * s, 440 * s + py * 0.75 * s)]
                self.c.create_polygon(*pts, fill=fill, outline=ACCENT, width=max(1, 1.2 * s))
        if not getattr(self, "has_box", False):
            Icon(self.c, "image", 125 * s, 240 * s, 60 * s, SOFT, 2.0 * s)
            self._text(155, 316, "No box art yet", 10, SOFT, anchor="n")
        if PICTURES and not self.slides.imgs:
            Icon(self.c, "film", 580 * s, 220 * s, 60 * s, SOFT, 2.0 * s)
            self._text(610, 296, "No screenshots yet. Run Skraper on your game folders, or let RetroArch download "
                                 "thumbnails, and the game's art and details appear here.", 10, SOFT, anchor="n", width=440)
        desc = " ".join((info.get("desc") or "").split())
        if desc:
            lines = textwrap.wrap(desc, 56)
            if len(lines) > 4:
                lines = lines[:4]
                lines[-1] = lines[-1][:52].rstrip(" .,;") + "..."
            self._text(300, 440, "\n".join(lines), 10, SOFT)
        # Play / Leave queue, bottom right; status line above it
        self.btn_bg = self.c.create_rectangle(0, 0, 0, 0, fill=TEXT, outline="", tags="btn")
        self.btn_icon = None
        self.btn_txt = self.c.create_text(0, 0, text="", font=(self.fam, int(13 * s), "bold"), fill="#111111", anchor="w", tags="btn")
        self.status = self._text(40, 476, "", 11, ACCENT, bold=True, tag="status")
        self.c.tag_bind("btn", "<Button-1>", lambda e: self.press())
        self.c.tag_bind("btn", "<Enter>", lambda e: (self.c.itemconfigure(self.btn_bg, fill=ACCENT), self.btn_icon and self.btn_icon.play()))
        self.c.tag_bind("btn", "<Leave>", lambda e: self.c.itemconfigure(self.btn_bg, fill=TEXT))
        self.set_state("idle", "")

    def _button(self, text, icon):
        s = self.s
        x1, y1, x2, y2 = 740 * s, 478 * s, 920 * s, 522 * s
        self.c.coords(self.btn_bg, x1, y1, x2, y2)
        if self.btn_icon:
            for it in self.btn_icon.items:
                self.c.delete(it)
        self.btn_icon = Icon(self.c, icon, x1 + 16 * s, y1 + 11 * s, 22 * s, "#111111", max(1.5, 2.2 * s))
        for it in self.btn_icon.items:
            self.c.addtag_withtag("btn", it)
        self.c.coords(self.btn_txt, x1 + 48 * s, (y1 + y2) / 2)
        self.c.itemconfigure(self.btn_txt, text=text.upper())
        self.c.tag_raise("btn")

    def set_state(self, state, text):
        if not self.alive:
            return
        self.state = state
        self._button(*{"idle": ("Play", "go"), "searching": ("Leave queue", "seek"), "session": ("End session", "exit")}.get(
            state, ("Play", "go")))
        msg = {"searching": "Looking for an opponent...", "session": "Playing"}.get(state, "")
        for it in self.c.find_withtag("status"):
            self.c.itemconfigure(it, text=msg)
        if state == "searching":
            self._seek_loop()

    def _seek_loop(self):
        if self.alive and self.state == "searching":
            self.btn_icon.play()
            self.win.after(1600, self._seek_loop)

    def press(self):
        self.sound("play" if self.state == "idle" else "back")
        self.on_play()

    def _focus(self, on):
        self.focused = on

    def _tick(self):
        if not self.alive:
            return
        if self.photo is not None:
            t = time.monotonic() - self.t_start
            if self.focused or t < 1.0:
                self.photo.paste(self._frame(t))
        self.win.after(70 if self.focused else 500, self._tick)

    def close(self):
        if not self.alive:
            return
        self.alive = False
        self.sound("back")
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_close:
            self.on_close()
