"""Box art, screenshots, a gameplay clip and details for a game, from what is already on this computer:
- gamelist.xml files and media folders written by Skraper (or EmulationStation, ES-DE, Batocera, Recalbox),
- ES-DE's downloaded_media folder,
- RetroArch's downloaded thumbnails.
Nothing is downloaded here. The Android core (GameInfo.java) uses the same rules; tests/test_gameinfo.py checks
both agree.
"""
import os
import re
import sys

import library

KINDS = ("box", "screen", "title", "fanart", "logo", "video")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".webm")
MAX_GAMELIST = 20 * 1024 * 1024

# media sub-folder name -> kind (Skraper, EmulationStation, ES-DE, Recalbox)
FOLDER_KINDS = {
    "box2dfront": "box", "box2d": "box", "box": "box", "boxes": "box", "boxart": "box", "boxfront": "box",
    "covers": "box", "cover": "box", "thumbnails": "box", "thumbnail": "box",
    "screenshots": "screen", "screenshot": "screen", "ss": "screen", "snaps": "screen", "snap": "screen",
    "images": "screen", "image": "screen", "mixrbv1": "screen", "mixrbv2": "screen", "mix": "screen",
    "titlescreens": "title", "titlescreen": "title", "sstitle": "title", "titles": "title",
    "fanart": "fanart", "fanarts": "fanart", "backgrounds": "fanart",
    "marquees": "logo", "marquee": "logo", "wheel": "logo", "wheels": "logo", "logos": "logo",
    "videos": "video", "video": "video", "downloaded_images": "screen", "downloaded_videos": "video",
}
# Batocera / Skraper "images/<rom>-<suffix>.png" names
SUFFIX_KINDS = {"thumb": "box", "boxart": "box", "box": "box", "image": "screen", "screenshot": "screen",
                "titleshot": "title", "title": "title", "fanart": "fanart", "marquee": "logo", "wheel": "logo",
                "video": "video"}
# gamelist.xml tags -> kind, best first
TAG_KINDS = [("boxart", "box"), ("thumbnail", "box"), ("cover", "box"),
             ("screenshot", "screen"), ("image", "screen"),
             ("titleshot", "title"),
             ("fanart", "fanart"),
             ("marquee", "logo"), ("wheel", "logo"),
             ("video", "video")]
TEXT_TAGS = ("name", "desc", "developer", "publisher", "genre", "players", "rating", "releasedate")

# RetroArch playlist names, and ES-DE system folder names
RETROARCH = {"gba": "Nintendo - Game Boy Advance", "ps1": "Sony - PlayStation", "ps2": "Sony - PlayStation 2",
             "ps3": "Sony - PlayStation 3", "dreamcast": "Sega - Dreamcast", "gamecube": "Nintendo - GameCube"}
RA_KINDS = {"Named_Boxarts": "box", "Named_Snaps": "screen", "Named_Titles": "title", "Named_Logos": "logo"}
ESDE = {"gba": "gba", "ps1": "psx", "ps2": "ps2", "ps3": "ps3", "dreamcast": "dreamcast", "gamecube": "gc"}
ESDE_KINDS = {"covers": "box", "screenshots": "screen", "titlescreens": "title", "fanart": "fanart",
              "marquees": "logo", "videos": "video"}

_ENT = {"lt": "<", "gt": ">", "amp": "&", "quot": '"', "apos": "'"}


def unescape(s):
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", lambda m: m.group(1).replace("&", "&amp;"), s, flags=re.S)

    def ent(m):
        e = m.group(1)
        try:
            if e[:2] in ("#x", "#X"):
                return chr(int(e[2:], 16))
            if e[:1] == "#":
                return chr(int(e[1:]))
        except (ValueError, OverflowError):
            return m.group(0)
        return _ENT.get(e, m.group(0))
    return re.sub(r"&(#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[a-zA-Z]+);", ent, s)


def norm(path):
    """One spelling for a path, to compare them: forward slashes, no ./ or ../, lower case."""
    parts = []
    for p in path.replace("\\", "/").split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if parts:
                parts.pop()
            continue
        parts.append(p)
    return ("/" if path.replace("\\", "/").startswith("/") else "") + "/".join(parts).lower()


def stem(path):
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    dot = base.rfind(".")
    return base[:dot] if dot > 0 else base


def parse_gamelist(xml, folder):
    """gamelist.xml text, the folder it is in -> [{"rom": normalized path, tag: text...}]. A small, strict reader:
    no DTDs or entities beyond the five standard ones, so a hostile file can't make it fetch or expand anything."""
    out = []
    for m in re.finditer(r"<game(?:\s[^>]*)?>(.*?)</game\s*>", xml, re.S):
        body = m.group(1)
        g = {}
        for t in re.finditer(r"<([A-Za-z][A-Za-z0-9_-]*)(?:\s[^>]*)?>(.*?)</\1\s*>", body, re.S):
            tag = t.group(1).lower()
            if tag not in g:
                g[tag] = unescape(t.group(2)).strip()
        path = g.pop("path", "")
        if not path:
            continue
        g["rom"] = norm(path if path.startswith("/") or re.match(r"^[A-Za-z]:", path) else folder + "/" + path)
        g["dir"] = folder
        out.append(g)
        if len(out) >= 50000:
            break
    return out


def media_kind(rel):
    """A media file's path below a system folder -> (kind, rom stem), or None. E.g. 'media/box2dfront/Game.png' ->
    ('box', 'Game'); 'images/Game-thumb.png' -> ('box', 'Game'); 'videos/Game-video.mp4' -> ('video', 'Game')."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if len(parts) < 2:
        return None
    name = parts[-1]
    ext = os.path.splitext(name)[1].lower()
    is_video = ext in VIDEO_EXTS
    if not is_video and ext not in IMAGE_EXTS:
        return None
    st = stem(name)
    folder = parts[-2].lower()
    m = re.match(r"^(.*)-(thumb|boxart|box|image|screenshot|titleshot|title|fanart|marquee|wheel|video)$", st)
    if m and folder in ("images", "videos", "downloaded_images", "downloaded_videos", "media"):
        kind = SUFFIX_KINDS[m.group(2)]
        st = m.group(1)
    elif folder in FOLDER_KINDS:
        kind = FOLDER_KINDS[folder]
    else:
        return None
    if (kind == "video") != is_video:
        return None
    return kind, st


def _year(date):
    m = re.match(r"^\s*([0-9]{4})", date or "")
    return m.group(1) if m and 1970 <= int(m.group(1)) <= 2100 else ""


def collect(rom, name, entries, media, resolve=lambda p: p):
    """Everything known for one game. rom: its file path ('' for a streamed game); name: its display name;
    entries: parse_gamelist() results; media: [(path, kind, stem)]; resolve: a gamelist's media path -> something
    the app can open, or None. -> info dict (missing parts empty)."""
    info = {"name": "", "desc": "", "year": "", "developer": "", "publisher": "", "genre": "", "players": "",
            "rating": "", "box": "", "screens": [], "title": "", "fanart": "", "logo": "", "video": ""}
    want_rom, want_stem = norm(rom) if rom else "", stem(rom).lower() if rom else ""
    want_clean = library.slug(library.clean_name(rom) if rom else name)
    rom_dir = want_rom.rsplit("/", 1)[0] if "/" in want_rom else ""

    def entry_rank(e):
        if want_rom and e["rom"] == want_rom:
            return 0
        e_stem = stem(e["rom"]).lower()
        e_dir = e["rom"].rsplit("/", 1)[0] if "/" in e["rom"] else ""
        if want_stem and e_stem == want_stem:
            return 1 if e_dir == rom_dir else 2
        if want_clean and library.slug(library.clean_name(e["rom"])) == want_clean:
            return 3
        if want_clean and library.slug(e.get("name", "")) == want_clean:
            return 4
        return None
    ranked = sorted((r, i) for i, r in enumerate(entry_rank(e) for e in entries) if r is not None)
    if ranked:
        e = entries[ranked[0][1]]
        for t in TEXT_TAGS:
            v = e.get(t, "")
            if t == "releasedate":
                info["year"] = _year(v)
            elif t == "rating":
                try:
                    r = float(v)
                    if r == r:
                        info["rating"] = f"{max(0.0, min(1.0, r)):.2f}"
                except ValueError:
                    pass
            else:
                info[t] = re.sub(r"[ \t]+", " ", v)[:4000]
        for tag, kind in TAG_KINDS:
            v = e.get(tag, "")
            if not v:
                continue
            where = resolve(v if v.startswith("/") or re.match(r"^[A-Za-z]:", v) else e["dir"] + "/" + v)
            if where:
                _put(info, kind, where)

    def media_rank(st):
        s = st.lower()
        if want_stem and s == want_stem:
            return 0
        if want_clean and library.slug(library.clean_name(st + ".x")) == want_clean:
            return 1
        return None
    for r, path, kind in sorted((media_rank(st), path, kind) for path, kind, st in media if media_rank(st) is not None):
        _put(info, kind, path)
    if not info["name"]:
        info["name"] = name
    return info


def _put(info, kind, path):
    if kind == "screen":
        if path not in info["screens"] and len(info["screens"]) < 8:
            info["screens"].append(path)
    elif not info[kind]:
        info[kind] = path


# ---- the desktop side: where to look on this computer

def _listing(folder, depth=2):
    """Media files below folder, at most depth levels down: [(path relative to folder, full path)]."""
    out = []
    try:
        top = os.path.abspath(folder)
        for dirpath, dirnames, filenames in os.walk(top):
            d = dirpath[len(top):].count(os.sep)
            if d >= depth:
                dirnames[:] = []
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in IMAGE_EXTS + VIDEO_EXTS:
                    full = os.path.join(dirpath, fn)
                    out.append((os.path.relpath(full, top), full))
            if len(out) > 100000:
                break
    except OSError:
        pass
    return out


def retroarch_dirs():
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        app = os.environ.get("APPDATA", "")
        return [os.path.join(app, "RetroArch"), r"C:\RetroArch-Win64", r"C:\RetroArch"]
    if sys.platform == "darwin":
        return [os.path.join(home, "Library", "Application Support", "RetroArch")]
    return [os.path.join(home, ".config", "retroarch"),
            os.path.join(home, ".var", "app", "org.libretro.RetroArch", "config", "retroarch"),
            os.path.join(home, "snap", "retroarch", "current", ".config", "retroarch")]


def esde_dirs():
    home = os.path.expanduser("~")
    return [os.path.join(home, "ES-DE", "downloaded_media"), os.path.join(home, ".emulationstation", "downloaded_media")]


def ra_name(s):
    """RetroArch's thumbnail file name for a game label."""
    return re.sub(r'[&*/:`<>?\\|"]', "_", s)


class Finder:
    """Looks up and remembers game info on this computer. extra: more RetroArch folders (e.g. next to its program)."""

    def __init__(self, extra_retroarch=(), art=None):
        self.cache, self.dirs = {}, {}
        self.ra = list(extra_retroarch) + retroarch_dirs()
        # what the app downloaded from ScreenScraper (screenscraper.py), laid out like Skraper's output
        self.art = art or os.path.join(os.path.expanduser("~"), ".netplay-matchmaker", "art")

    def find(self, game):
        key = (game.get("system"), game.get("name"), game.get("rom", ""))
        if key not in self.cache:
            self.cache[key] = self._find(game)
        return self.cache[key]

    def _listing(self, folder, depth):
        if folder not in self.dirs:
            self.dirs[folder] = _listing(folder, depth)
        return self.dirs[folder]

    def _find(self, game):
        rom, system, name = game.get("rom", "") or "", game.get("system", ""), game.get("name", "")
        entries, media, seen = [], [], set()
        if rom:
            d = os.path.dirname(os.path.abspath(rom))
            for up in range(3):          # the game's folder, its parent and grandparent (Skraper writes per system)
                gl = os.path.join(d, "gamelist.xml")
                if os.path.isfile(gl) and gl not in seen:
                    seen.add(gl)
                    try:
                        if os.path.getsize(gl) <= MAX_GAMELIST:
                            with open(gl, encoding="utf-8", errors="replace") as f:
                                entries += parse_gamelist(f.read(), d.replace("\\", "/"))
                    except OSError:
                        pass
                for sub in ("media", "images", "videos", "downloaded_images", "downloaded_videos"):
                    sd = os.path.join(d, sub)
                    if os.path.isdir(sd) and sd not in seen:
                        seen.add(sd)
                        for rel, full in self._listing(sd, 2):
                            k = media_kind(sub + "/" + rel)
                            if k:
                                media.append((full, k[0], k[1]))
                nd = os.path.dirname(d)
                if nd == d:
                    break
                d = nd
        for base in esde_dirs():
            sd = os.path.join(base, ESDE.get(system, system))
            for folder, kind in ESDE_KINDS.items():
                fd = os.path.join(sd, folder)
                if os.path.isdir(fd):
                    for rel, full in self._listing(fd, 1):
                        media.append((full, kind, stem(rel)))
        sd = os.path.join(self.art, re.sub(r"[^a-z0-9]", "", system) or "other")
        gl = os.path.join(sd, "gamelist.xml")
        if os.path.isfile(gl):
            try:
                with open(gl, encoding="utf-8", errors="replace") as f:
                    entries += parse_gamelist(f.read(), sd.replace("\\", "/"))
            except OSError:
                pass
        for rel, full in self._listing(os.path.join(sd, "media"), 2):
            k = media_kind("media/" + rel)
            if k:
                media.append((full, k[0], k[1]))
        label = stem(rom) if rom else name
        for base in self.ra:
            sd = os.path.join(base, "thumbnails", RETROARCH.get(system, ""))
            for folder, kind in RA_KINDS.items():
                for want in {ra_name(label), ra_name(name)}:
                    p = os.path.join(sd, folder, want + ".png")
                    if os.path.isfile(p):
                        media.append((p, kind, want))
        return collect(rom, name, entries, media, lambda p: os.path.normpath(p) if os.path.isfile(p) else None)
