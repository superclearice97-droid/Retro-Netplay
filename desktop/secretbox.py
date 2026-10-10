"""Keeps the Discord webhook and Telegram bot token out of the plain config file.

Where the computer offers a secure store, the secret goes there and the config file only holds a marker:
- Windows: encrypted with DPAPI, so only your Windows account on this PC can decrypt it.
- macOS: saved in your login Keychain.
- Linux: saved in the desktop keyring (GNOME Keyring / KWallet) through `secret-tool`, when installed.
Anywhere else the secret stays in the config file, which is readable only by your user account.
"""
import base64
import platform
import shutil
import subprocess

SERVICE = "netplay-matchmaker"
KEYCHAIN = "keychain:"
DPAPI = "dpapi:"


# ----------------------------------------------------------------- Windows DPAPI
def _dpapi(data, encrypt):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    crypt = ctypes.windll.crypt32
    fn = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    # flags 0x1 = CRYPTPROTECT_UI_FORBIDDEN
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


# ----------------------------------------------------------------- macOS / Linux keyrings
def _run(argv, stdin=None):
    r = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or "keyring command failed")
    return r.stdout


def _keyring_kind():
    sysname = platform.system()
    if sysname == "Darwin" and shutil.which("security"):
        return "mac"
    if sysname == "Linux" and shutil.which("secret-tool"):
        return "linux"
    return None


def _kr_set(kind, name, value):
    if kind == "mac":
        # Sent through `security -i` on stdin so the secret never appears in the process list.
        if any(c in value for c in "\"'\\\n\r"):
            raise OSError("unsupported characters")
        _run(["security", "-i"], stdin=f'add-generic-password -U -s {SERVICE} -a {name} -w "{value}"\n')
    else:
        _run(["secret-tool", "store", "--label", f"Netplay Matchmaker {name}", "service", SERVICE, "name", name],
             stdin=value)


def _kr_get(kind, name):
    if kind == "mac":
        return _run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"]).rstrip("\n")
    return _run(["secret-tool", "lookup", "service", SERVICE, "name", name])


def _kr_del(kind, name):
    try:
        if kind == "mac":
            _run(["security", "delete-generic-password", "-s", SERVICE, "-a", name])
        else:
            _run(["secret-tool", "clear", "service", SERVICE, "name", name])
    except (OSError, subprocess.SubprocessError):
        pass


# ----------------------------------------------------------------- public
def protect(name, value):
    """-> what to write in the config file for this secret."""
    value = value or ""
    kind = _keyring_kind()
    if not value:
        if kind:
            _kr_del(kind, name)
        return ""
    try:
        if platform.system() == "Windows":
            return DPAPI + base64.b64encode(_dpapi(value.encode(), True)).decode()
        if kind:
            _kr_set(kind, name, value)
            return KEYCHAIN + name
    except (OSError, subprocess.SubprocessError, AttributeError):
        pass
    return value    # no secure store here: kept in the private (owner-only) config file


def reveal(name, stored):
    """-> the real secret from what the config file holds ("" if it can't be read back)."""
    stored = stored or ""
    try:
        if stored.startswith(DPAPI):
            return _dpapi(base64.b64decode(stored[len(DPAPI):]), False).decode()
        if stored.startswith(KEYCHAIN):
            kind = _keyring_kind()
            return _kr_get(kind, name) if kind else ""
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError):
        return ""
    return stored


def forget(name):
    kind = _keyring_kind()
    if kind:
        _kr_del(kind, name)
