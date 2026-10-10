"""Game folders: find every game in the folders a player picks, name it the same way on every device,
and work out which system it belongs to.

The name matters: two players are matched when their game keys agree (system + name slug), so the
desktop and Android apps clean file names with exactly the same rules (see android/core Library.java,
and tests/test_library.py, which checks both give the same answers).
"""
import os
import re

# extension -> systems it could belong to (first is the most likely)
EXT_SYSTEMS = {
    ".gba": ["gba"],
    ".cue": ["ps1"], ".pbp": ["ps1"], ".m3u": ["ps1"],
    ".gdi": ["dreamcast"], ".cdi": ["dreamcast"],
    ".gcm": ["gamecube"], ".rvz": ["gamecube"], ".gcz": ["gamecube"], ".wbfs": ["gamecube"], ".ciso": ["gamecube"],
    ".cso": ["ps2"],
    ".iso": ["ps2", "gamecube", "ps3"],
    ".chd": ["ps1", "ps2", "dreamcast"],
    ".bin": ["ps2", "ps3"],
}
# preferred file when the same game appears in several formats
EXT_RANK = [".m3u", ".chd", ".pbp", ".cue", ".gdi", ".cdi", ".rvz", ".gcz", ".wbfs", ".ciso", ".gcm", ".cso", ".iso", ".bin", ".gba"]

# folder-name hints (checked on every folder between the game and the folder the player picked)
FOLDER_HINTS = [
    ("gba", ["gba", "gameboy advance", "game boy advance", "gameboyadvance"]),
    ("ps2", ["ps2", "playstation 2", "playstation2", "pcsx2"]),
    ("ps3", ["ps3", "playstation 3", "playstation3", "rpcs3"]),
    ("ps1", ["ps1", "psx", "psone", "ps one", "playstation 1", "playstation1", "playstation"]),
    ("dreamcast", ["dreamcast", "dc"]),
    ("gamecube", ["gamecube", "game cube", "gc", "ngc", "wii", "dolphin"]),
]

MAX_DEPTH = 6
MAX_FILES = 50000


def clean_name(filename):
    """'Legend of Zelda, The - The Minish Cap (USA) [!].gba' -> 'The Legend of Zelda - The Minish Cap'."""
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    dot = base.rfind(".")
    if dot > 0:
        base = base[:dot]
    base = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", base)
    base = base.replace("_", " ")
    if " " not in base.strip():
        base = base.replace(".", " ")
    base = re.sub(r"^\s*\d{3,5}\s*-\s*", "", base)
    base = re.sub(r"\s+", " ", base).strip(" -.")
    m = re.match(r"^(.*?), (The|A|An)(\s+-\s+.*)?$", base)
    if m:
        base = f"{m.group(2)} {m.group(1)}{m.group(3) or ''}"
    return base


def _words(s):
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() + " "


def hint_for(folder_names):
    """System suggested by folder names (nearest folder wins), or None."""
    for name in reversed(folder_names):
        w = _words(name)
        for system, keys in FOLDER_HINTS:
            if any(f" {k} " in w for k in keys):
                return system
    return None


def sniff(head):
    """Look at the first ~40 KB of an .iso/.bin: returns a system id or None."""
    if len(head) >= 0x20:
        if head[0x1C:0x20] == b"\xC2\x33\x9F\x3D" or head[0x18:0x1C] == b"\x5D\x1C\x9E\xA3":
            return "gamecube"          # GameCube or Wii disc header
    for pvd in (0x8000, 0x9310, 0x9318):   # plain ISO, or raw 2352-byte sectors (MODE1 / MODE2)
        block = head[pvd:pvd + 0x100]
        if block[1:6] == b"CD001":
            if b"PS3VOLUME" in block or b"PS3_GAME" in head:
                return "ps3"
            if b"PLAYSTATION" in block[8:40]:
                return "ps2"
    return None


SNIFF_BYTES = 0x9318 + 0x100


def classify(name, folder_names, head=None, siblings=()):
    """-> (system or None, candidates). None with candidates means: ask the player once for this folder."""
    ext = os.path.splitext(name)[1].lower()
    cands = EXT_SYSTEMS.get(ext)
    if not cands:
        return None, []
    if ext == ".bin" and os.path.splitext(name)[0].lower() + ".cue" in siblings:
        return None, []                # a track of a .cue game; the .cue is the game
    if len(cands) == 1:
        return cands[0], cands
    if head is not None:
        s = sniff(head)
        if s in cands:
            return s, cands
    h = hint_for(folder_names)
    if h in cands:
        return h, cands
    return None, cands


def _rank(path):
    ext = os.path.splitext(path)[1].lower()
    return EXT_RANK.index(ext) if ext in EXT_RANK else len(EXT_RANK)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def scan(folders, answers=None, read_head=None):
    """Walk the folders. answers: {"<folder>|<ext>": system or "skip"} from earlier questions.
    -> (games, questions). games: [{name, system, rom, auto}]. questions: [{key, folder, ext, candidates, count}]."""
    answers = answers or {}
    read_head = read_head or _read_head
    found, questions, seen = {}, {}, 0
    for top in folders:
        top = os.path.abspath(os.path.expanduser(top))
        if not os.path.isdir(top):
            continue
        base_depth = top.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            if dirpath.rstrip(os.sep).count(os.sep) - base_depth >= MAX_DEPTH:
                dirnames[:] = []
            rel = os.path.relpath(dirpath, os.path.dirname(top))
            folder_names = [p for p in rel.split(os.sep) if p not in (".", "")]
            lower = {f.lower() for f in filenames}
            for fn in filenames:
                seen += 1
                if seen > MAX_FILES:
                    break
                ext = os.path.splitext(fn)[1].lower()
                if ext not in EXT_SYSTEMS:
                    continue
                full = os.path.join(dirpath, fn)
                head = read_head(full) if ext in (".iso", ".bin") else None
                system, cands = classify(fn, folder_names, head, lower)
                if system is None and cands:
                    qkey = f"{dirpath}|{ext}"
                    ans = answers.get(qkey)
                    if ans == "skip":
                        continue
                    if ans in cands:
                        system = ans
                    else:
                        q = questions.setdefault(qkey, {"key": qkey, "folder": dirpath, "ext": ext, "candidates": cands, "count": 0})
                        q["count"] += 1
                        continue
                if system is None:
                    continue
                name = clean_name(fn)
                if not name:
                    continue
                k = f"{system}:{slug(name)}"
                if k not in found or (_rank(full), full) < (_rank(found[k]["rom"]), found[k]["rom"]):
                    found[k] = {"name": name, "system": system, "rom": full, "auto": True}
    games = sorted(found.values(), key=lambda g: (g["system"], g["name"].lower()))
    return games, list(questions.values())


def _read_head(path):
    try:
        with open(path, "rb") as f:
            return f.read(SNIFF_BYTES)
    except OSError:
        return b""


def merge(existing, scanned):
    """Keep games the player added by hand; replace the automatic ones with the new scan."""
    manual = [g for g in existing if not g.get("auto")]
    keys = {f"{g['system']}:{slug(g['name'])}" for g in manual}
    return manual + [g for g in scanned if f"{g['system']}:{slug(g['name'])}" not in keys]
