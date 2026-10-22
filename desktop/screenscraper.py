"""Optional: download a game's box art, screenshots, clip and details from ScreenScraper (screenscraper.fr), the
free game database that Skraper uses.

Off until the player turns it on, agrees to what is sent, and signs in with their own free ScreenScraper account.
For each game it sends ScreenScraper: the system, the game file's name and size, its CRC32 checksum (files up to
64 MB), and the player's ScreenScraper login. Nothing else. The pictures and details are saved in this app's own
folder (art/<system>/, laid out like Skraper's, with a gamelist.xml), never in the player's game folders, and
gameinfo.Finder reads them like any other Skraper output. The art belongs to its owners: it's for the player's own
library only.

ScreenScraper needs every app to have its own developer ID. It is put into the build from the project's CI secrets
(SS_DEVID, SS_DEVPASSWORD -> ss_dev.py); a build without one says the feature isn't available in this version.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from xml.sax.saxutils import escape

import library

API = "https://api.screenscraper.fr/api2/"
SOFT = "Retro-Netplay"
SITE = "https://www.screenscraper.fr/"
try:
    from ss_dev import DEVID, DEVPASSWORD           # written by the build, never committed
except ImportError:
    DEVID = DEVPASSWORD = ""
SYSTEM_IDS = {"gba": 12, "gamecube": 13, "dreamcast": 23, "ps1": 57, "ps2": 58, "ps3": 59}
CRC_MAX = 64 * 1024 * 1024
MAX_IMAGE, MAX_VIDEO = 8 * 1024 * 1024, 40 * 1024 * 1024
REGIONS = ["wor", "us", "eu", "ss", "uk", "fr", "de", "jp"]
# ScreenScraper media type -> (kind, media sub-folder, gamelist tag), best first within a kind
MEDIA = [("box-2D", "box", "box2dfront", "thumbnail"), ("ss", "screen", "images", "image"),
         ("sstitle", "title", "titlescreens", "titleshot"), ("wheel-hd", "logo", "wheel", "marquee"),
         ("wheel", "logo", "wheel", "marquee"), ("fanart", "fanart", "fanart", "fanart"),
         ("video-normalized", "video", "videos", "video"), ("video", "video", "videos", "video")]
CONSENT = ("To find your games' box art, screenshots, gameplay clips and details, Retro-Netplay will send "
           "ScreenScraper (screenscraper.fr, a free game database run from France) these things for each game you "
           "choose: its system, its file name and size, a checksum of the file (for files up to 64 MB), and your "
           "ScreenScraper username and password. Nothing else about you or your computer is sent by the app; "
           "ScreenScraper sees your internet address, as any website does. ScreenScraper's own rules and privacy "
           "policy apply (see screenscraper.fr). What you download is saved only on this computer. Game art belongs "
           "to its owners: keep it for your own game library.")


class ScrapeError(Exception):
    def __init__(self, msg, why=""):
        super().__init__(msg)
        self.why = why


def available():
    return bool(DEVID and DEVPASSWORD)


def _why(code, body):
    """ScreenScraper's HTTP answers -> (reason, plain words)."""
    if code == 404:
        return "not_found", "ScreenScraper doesn't know this game (yet)."
    if code in (401, 423):
        return "closed", "ScreenScraper is closed to apps right now (it does this when it's busy). Try again later."
    if code == 403:
        return "dev", "ScreenScraper turned down this app's developer ID. Update Retro-Netplay, then try again."
    if code == 426:
        return "outdated", "ScreenScraper no longer accepts this version of Retro-Netplay. Update it, then try again."
    if code == 429:
        return "busy", "ScreenScraper says too many downloads are running at once. Try again in a minute."
    if code == 430:
        return "quota", "You've used today's ScreenScraper allowance. It resets tomorrow (a ScreenScraper membership gives more)."
    if code == 431:
        return "unknown_roms", "ScreenScraper didn't recognise many of today's files, so it paused. Try again tomorrow."
    if "identifiant" in body.lower() or "login" in body.lower():
        return "login", "ScreenScraper didn't accept your username or password. Check them in Settings."
    return "error", f"ScreenScraper answered with an error ({code})."


def _get(url, opener, limit):
    try:
        with opener(urllib.request.Request(url, headers={"User-Agent": SOFT}), timeout=30) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(2000).decode("utf-8", "replace")
        except Exception:
            pass
        why, msg = _why(e.code, body)
        raise ScrapeError(msg, why) from None
    except (urllib.error.URLError, OSError) as e:
        raise ScrapeError(f"Couldn't reach ScreenScraper ({getattr(e, 'reason', e)}). Check your internet connection.", "network") from None
    if len(data) > limit:
        raise ScrapeError("ScreenScraper sent more than expected; it was left out.", "too_big")
    return data


def _crc(path):
    c = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            c = zlib.crc32(chunk, c)
    return "%08X" % (c & 0xFFFFFFFF)


def query(game, user, password):
    """The jeuInfos.php (file) or jeuRecherche.php (name only) URL for a game. Contains the logins: never log it."""
    p = {"devid": DEVID, "devpassword": DEVPASSWORD, "softname": SOFT, "output": "json", "ssid": user, "sspassword": password}
    system = SYSTEM_IDS.get(game.get("system"))
    if system:
        p["systemeid"] = system
    rom = game.get("rom") or ""
    if rom and os.path.isfile(rom):
        size = os.path.getsize(rom)
        p.update({"romtype": "rom", "romnom": os.path.basename(rom), "romtaille": size})
        if size <= CRC_MAX:
            p["crc"] = _crc(rom)
        return API + "jeuInfos.php?" + urllib.parse.urlencode(p)
    p["recherche"] = game.get("name", "")
    return API + "jeuRecherche.php?" + urllib.parse.urlencode(p)


def _pick(items, key="region", prefer=REGIONS):
    """The best entry of a ScreenScraper list ([{region or langue, text}]) for an English-speaking player."""
    if not isinstance(items, list) or not items:
        return None
    for want in prefer:
        for it in items:
            if isinstance(it, dict) and it.get(key) == want:
                return it
    return items[0] if isinstance(items[0], dict) else None


def _text(v):
    if isinstance(v, dict):
        return str(v.get("text", "")).strip()
    return ""


def _ours(url):
    """Only https from ScreenScraper itself: the request carries the player's login."""
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "screenscraper.fr" or host.endswith(".screenscraper.fr"))


def details(jeu):
    """ScreenScraper's 'jeu' object -> gamelist fields (name, desc, releasedate, developer, publisher, genre,
    players, rating) and [(kind, sub-folder, tag, url, extension)] for the media, best first."""
    out = {}
    n = _pick(jeu.get("noms"))
    if n:
        out["name"] = str(n.get("text", "")).strip()
    s = _pick(jeu.get("synopsis"), "langue", ["en", "fr", "de", "es"])
    if s:
        out["desc"] = str(s.get("text", "")).strip()
    d = _pick(jeu.get("dates"))
    if d:
        m = re.match(r"^\s*([0-9]{4})(?:-([0-9]{2}))?(?:-([0-9]{2}))?", str(d.get("text", "")))
        if m:
            out["releasedate"] = m.group(1) + (m.group(2) or "01") + (m.group(3) or "01") + "T000000"
    out["developer"], out["publisher"] = _text(jeu.get("developpeur")), _text(jeu.get("editeur"))
    out["players"] = _text(jeu.get("joueurs"))
    try:
        r = float(_text(jeu.get("note")))
        if r == r:
            out["rating"] = "%.2f" % (max(0.0, min(20.0, r)) / 20)
    except ValueError:
        pass
    for g in jeu.get("genres") or []:
        gn = _pick(g.get("noms") if isinstance(g, dict) else None, "langue", ["en", "fr"])
        if gn:
            out["genre"] = str(gn.get("text", "")).strip()
            break
    media, seen = [], set()
    items = [m for m in (jeu.get("medias") or []) if isinstance(m, dict)]
    for stype, kind, sub, tag in MEDIA:
        if kind in seen:
            continue
        cands = [m for m in items if m.get("type") == stype and _ours(str(m.get("url", "")))]
        best = _pick(cands) if cands else None
        if best:
            ext = re.sub(r"[^a-z0-9]", "", str(best.get("format", "png")).lower())[:5] or "png"
            media.append((kind, sub, tag, best["url"], ext))
            seen.add(kind)
    return {k: v for k, v in out.items() if v}, media


def lookup(game, user, password, opener=urllib.request.urlopen):
    """Ask ScreenScraper about one game -> its 'jeu' object. Raises ScrapeError in plain words."""
    if not available():
        raise ScrapeError("Downloading art from ScreenScraper isn't available in this version of Retro-Netplay.", "nodev")
    if not user or not password:
        raise ScrapeError("Add your ScreenScraper username and password in Settings first (an account is free).", "login")
    raw = _get(query(game, user, password), opener, 4 * 1024 * 1024)
    try:
        resp = json.loads(raw.decode("utf-8", "replace"))["response"]
    except (ValueError, KeyError, TypeError):
        raise ScrapeError("ScreenScraper's answer couldn't be read. Try again later.", "error") from None
    jeu = resp.get("jeu")
    if not isinstance(jeu, dict):
        jeux = [j for j in (resp.get("jeux") or []) if isinstance(j, dict) and j.get("id")]
        jeu = jeux[0] if jeux else None
    if not isinstance(jeu, dict):
        raise ScrapeError("ScreenScraper doesn't know this game (yet).", "not_found")
    return jeu


def art_root():
    return os.path.join(os.path.expanduser("~"), ".netplay-matchmaker", "art")


def scrape(game, user, password, root=None, opener=urllib.request.urlopen, videos=True, pause=1.2):
    """Download one game's details and media into root/<system>/ (gamelist.xml + media/<kind>/). -> [kinds saved]."""
    root = root or art_root()
    jeu = lookup(game, user, password, opener)
    fields, media = details(jeu)
    system = re.sub(r"[^a-z0-9]", "", game.get("system", "")) or "other"
    sd = os.path.join(root, system)
    rom = game.get("rom") or ""
    stem = os.path.splitext(os.path.basename(rom))[0] if rom else library.slug(game.get("name", "")) or "game"
    stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", stem)[:150]
    saved, tags = [], {}
    for kind, sub, tag, url, ext in media:
        if kind == "video" and not videos:
            continue
        time.sleep(pause)                     # one download at a time, gently (ScreenScraper's free accounts allow one)
        try:
            data = _get(url, opener, MAX_VIDEO if kind == "video" else MAX_IMAGE)
        except ScrapeError as e:
            if e.why in ("quota", "busy", "closed", "dev", "outdated", "login", "network"):
                raise
            continue
        if kind != "video" and not (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[8:12] == b"WEBP"):
            continue                          # not a picture: left out
        os.makedirs(os.path.join(sd, "media", sub), exist_ok=True)
        path = os.path.join(sd, "media", sub, f"{stem}.{ext}")
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        tags[tag] = f"./media/{sub}/{stem}.{ext}"
        saved.append(kind)
    _write_gamelist(sd, rom or f"./{stem}.game", fields, tags)
    return saved


def _write_gamelist(sd, rom, fields, tags):
    """Add or replace this game in root/<system>/gamelist.xml (Skraper's format, read back by gameinfo.py)."""
    import gameinfo
    os.makedirs(sd, exist_ok=True)
    gl = os.path.join(sd, "gamelist.xml")
    games = []
    if os.path.isfile(gl):
        with open(gl, encoding="utf-8", errors="replace") as f:
            games = gameinfo.parse_gamelist(f.read(), sd.replace("\\", "/"))
    want = gameinfo.norm(rom if rom.startswith("/") or re.match(r"^[A-Za-z]:", rom) else sd + "/" + rom)
    keep = [g for g in games if g["rom"] != want]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<gameList>"]
    for g in keep:
        lines.append(_game_xml(g["rom"], g))
    lines.append(_game_xml(rom, {**fields, **tags}))
    lines.append("</gameList>")
    with open(gl + ".part", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(gl + ".part", gl)


def _game_xml(path, g):
    order = ["name", "desc", "releasedate", "developer", "publisher", "genre", "players", "rating",
             "thumbnail", "image", "titleshot", "marquee", "fanart", "video"]
    parts = [f"<path>{escape(path)}</path>"] + [f"<{k}>{escape(str(g[k]))}</{k}>" for k in order if g.get(k)]
    return "  <game>" + "".join(parts) + "</game>"
