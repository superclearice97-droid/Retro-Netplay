"""Invite links and opt-in announcements. Pure logic: no GUI.

An invite is a netplay:// app link (it opens Retro-Netplay straight away), and a short code a friend can type
under Enter code instead. The code makes a private room on the matchmaker, so a friend who follows the link
meets the person who shared it. Announcing means posting that link to places the player chose to connect.
"""
import json
import re
import secrets
import socket
import time
import urllib.request
from urllib.parse import parse_qs, quote, urlencode, urlsplit

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no 0/O/1/I so codes survive being read aloud
CODE_RE = re.compile(r"^[A-Za-z0-9]{4,32}$")
GAME_RE = re.compile(r"^[a-z0-9]+:[a-z0-9-]+$")
HOST_RE = re.compile(r"^[A-Za-z0-9.\-\[\]:]{1,255}$")
LOCAL_HOSTS = ("", "localhost", "127.0.0.1", "::1", "0.0.0.0")

DISCORD_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/",
                    "https://ptb.discord.com/api/webhooks/", "https://canary.discord.com/api/webhooks/")
TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{20,}$")
TELEGRAM_CHAT_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z0-9_]{4,64})$")
DEFAULT_TEMPLATE = "Looking for a {game} match! Join me: {link}"

DEFAULTS = {
    "invite": {"enabled": True, "open_to_randoms": True, "host": "", "auto_announce": False,
               "cooldown_min": 15, "template": DEFAULT_TEMPLATE, "last_announce": 0},
    "social": {"discord_webhook": "", "telegram_token": "", "telegram_chat": ""},
}


def fill_defaults(cfg):
    for k, d in DEFAULTS.items():
        cur = cfg.get(k) if isinstance(cfg.get(k), dict) else {}
        cfg[k] = {**d, **cur}


def new_code(n=8):
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def link_host(cfg):
    """The address other people's devices use to reach your matchmaker. Never 127.0.0.1 (that would point
    every other device at itself): when the matchmaker runs on this computer, its home-network address is used."""
    h = (cfg["invite"]["host"].strip() or cfg["server_host"]).strip()
    if h.lower() in LOCAL_HOSTS or h.startswith("127."):
        import lan
        h = lan.lan_ip() or h
    return h


def link_is_shareable(cfg):
    """False while the address is only reachable from this computer."""
    h = link_host(cfg).lower()
    return h not in LOCAL_HOSTS and not h.startswith(("192.168.", "10.", "172.16.", "169.254."))


def build_link(cfg, game_key, code):
    """The app link a friend clicks: it opens Retro-Netplay and joins (no web page in between)."""
    return "netplay://join?" + urlencode({"host": link_host(cfg), "port": int(cfg["server_port"]), "g": game_key, "c": code})


INVITE_CODE_RE = re.compile(r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$")


def pretty_code(code):
    """ABCD2345 -> ABCD-2345 (easier to read out)."""
    return code[:4] + "-" + code[4:] if len(code) == 8 else code


def typed_code(text):
    """What someone typed under Enter code -> an invite code (8 characters), or ''."""
    c = re.sub(r"[\s-]", "", str(text or "")).upper()
    return c if INVITE_CODE_RE.match(c) else ""


def lookup_code(host, port, code, timeout=5):
    """Ask the matchmaker which game is waiting under this invite code. -> game key, '' (no such invite right now),
    or None (couldn't reach the matchmaker)."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            s.sendall((json.dumps({"v": 1, "op": "invite", "code": code}) + "\n").encode())
            s.settimeout(timeout)
            reply = json.loads(s.makefile("rb").readline(8192) or b"{}")
    except (OSError, ValueError):
        return None
    if reply.get("op") != "invite":
        return None
    game = reply.get("game") or ""
    return game if reply.get("found") and GAME_RE.match(game) else ""


def parse_link(text):
    """-> {"host","port","game","code"} or None. Accepts the web link or the netplay:// link."""
    try:
        u = urlsplit((text or "").strip())
        q = {k: v[0] for k, v in parse_qs(u.query).items() if v}
        if u.scheme == "netplay" and u.netloc == "join":
            host, port = q.get("host", ""), q.get("port", "")
        elif u.scheme in ("http", "https") and u.path == "/join":
            host = u.hostname or ""
            port = q.get("p") or (str((u.port or 0) - 1) if u.port else "")
        else:
            return None
        game, code = q.get("g", ""), q.get("c", "")
        if not (HOST_RE.match(host) and port.isdigit() and 0 < int(port) < 65536
                and GAME_RE.match(game) and CODE_RE.match(code)):
            return None
        return {"host": host, "port": int(port), "game": game, "code": code}
    except ValueError:
        return None


def message_text(cfg, game_name, link):
    t = (cfg["invite"].get("template") or DEFAULT_TEMPLATE)[:300]
    return t.replace("{game}", game_name[:80]).replace("{link}", link)[:500]


# ----------------------------------------------------------------- announcing
def _https_post(url, payload):
    """POST JSON over https only. Returns the HTTP status."""
    if not url.startswith("https://"):
        raise ValueError("refusing a non-https address")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "NetplayMatchmaker/1"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status


def services(cfg):
    """[(name, url, payload_builder)] for every connected service that passes validation."""
    s, out = cfg["social"], []
    hook = s["discord_webhook"].strip()
    if hook:
        out.append(("Discord", hook if hook.startswith(DISCORD_PREFIXES) else None, lambda text: {"content": text}))
    tok, chat = s["telegram_token"].strip(), s["telegram_chat"].strip()
    if tok or chat:
        ok = bool(TELEGRAM_TOKEN_RE.match(tok)) and bool(TELEGRAM_CHAT_RE.match(chat))
        out.append(("Telegram", f"https://api.telegram.org/bot{tok}/sendMessage" if ok else None,
                    lambda text, c=chat: {"chat_id": c, "text": text, "disable_web_page_preview": False}))
    return out


def announce(cfg, game_name, link, force=False, post=_https_post, now=None):
    """Post the invite to every connected service. Returns [(service, ok, note)].
    Respects the cooldown unless force=True (a manual 'send now'). Never includes anything but the text."""
    now = time.time() if now is None else now
    inv = cfg["invite"]
    if not force and now - float(inv.get("last_announce", 0)) < float(inv["cooldown_min"]) * 60:
        mins = int((float(inv["cooldown_min"]) * 60 - (now - float(inv["last_announce"]))) / 60) + 1
        return [("All", False, f"skipped: you posted recently (next auto-post allowed in about {mins} min)")]
    svcs = services(cfg)
    if not svcs:
        return [("All", False, "no social account is connected")]
    text, results = message_text(cfg, game_name, link), []
    for name, url, build in svcs:
        if url is None:
            results.append((name, False, "the saved address or token doesn't look right"))
            continue
        try:
            status = post(url, build(text))
            results.append((name, 200 <= status < 300, f"HTTP {status}"))
        except Exception as e:  # never let a failed post break matchmaking
            results.append((name, False, type(e).__name__))
    if any(ok for _, ok, _ in results):
        inv["last_announce"] = now
    return results
