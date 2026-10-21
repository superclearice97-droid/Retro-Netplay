"""Encrypted connections to a matchmaker on the internet (see PROTOCOL.md, "Encryption").

A matchmaker whose operator set up a certificate (NETPLAY_TLS_CERT / NETPLAY_TLS_KEY) accepts {"op":"starttls"} as
the first line and then speaks TLS. The apps ask for it whenever the matchmaker is reached by its domain name (the
name a certificate is made for) and check the certificate. Home-network matchmakers are reached by address and have
no certificate, so those connections stay plain. Once a matchmaker has been seen with TLS, the app never talks to it
in plain text again, so nobody in between can quietly switch it off.
"""
import ipaddress
import json
import os
import socket
import ssl
from pathlib import Path

SEEN = Path.home() / ".netplay-matchmaker" / "tls-matchmakers.json"
CA_FILE = ""          # (automatic tests: an extra certificate authority to trust)
CA_BUNDLES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt", "/etc/ssl/ca-bundle.pem",
              "/etc/ssl/cert.pem")
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".localdomain")


class TlsError(OSError):
    """The matchmaker's encryption didn't check out (wrong certificate, or it stopped offering TLS)."""


def wants_tls(host):
    """Use TLS with this matchmaker? Yes for internet names; no for addresses and home-network names."""
    h = str(host).strip().strip("[]").lower()
    if not h or h == "localhost" or h.endswith(LOCAL_SUFFIXES) or "." not in h:
        return False
    try:
        ipaddress.ip_address(h)
        return False                     # an address: certificates are made for names
    except ValueError:
        return True


def context():
    ctx = ssl.create_default_context()
    if CA_FILE:
        ctx.load_verify_locations(CA_FILE)
    elif not ctx.get_ca_certs():         # a packaged app may not find this system's certificates by itself
        for f in CA_BUNDLES:
            if os.path.exists(f):
                ctx.load_verify_locations(f)
                break
    return ctx


def _seen():
    try:
        return set(json.loads(SEEN.read_text()))
    except (OSError, ValueError, TypeError):
        return set()


def _remember(host):
    s = _seen()
    if host in s:
        return
    try:
        SEEN.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        SEEN.write_text(json.dumps(sorted(s | {host})))
    except OSError:
        pass


def _line(sock):
    """One line, read a byte at a time (nothing past it: the TLS handshake follows right after)."""
    out = b""
    while not out.endswith(b"\n") and len(out) < 4096:
        b = sock.recv(1)
        if not b:
            break
        out += b
    return out


def negotiate(host, port, timeout=8):
    """-> (plain socket, ssl context or None). With a context, the caller must wrap the socket in TLS next
    (wrap_socket, or asyncio's open_connection(sock=..., ssl=..., server_hostname=host))."""
    sock = socket.create_connection((host, int(port)), timeout)
    if not wants_tls(host):
        return sock, None
    try:
        sock.settimeout(timeout)
        sock.sendall(b'{"v":1,"op":"starttls"}\n')
        reply = json.loads(_line(sock) or b"{}")
    except (OSError, ValueError):
        reply = {}
    if reply.get("op") == "starttls" and reply.get("ok") is True:
        return sock, context()
    if host.lower() in _seen():
        sock.close()
        raise TlsError(f"The matchmaker {host} used to be encrypted and now isn't. Someone may be in between; "
                       "not connecting. (If its operator removed the certificate on purpose, delete "
                       f"{SEEN} to allow it.)")
    if reply.get("op") != "starttls":     # an older matchmaker that didn't understand: start over in plain text
        sock.close()
        return socket.create_connection((host, int(port)), timeout), None
    return sock, None                     # it answered "no TLS here": carry on in plain text on this connection


def connect(host, port, timeout=8):
    """A connected socket to the matchmaker, encrypted when it offers TLS (see negotiate)."""
    sock, ctx = negotiate(host, port, timeout)
    if ctx is None:
        return sock
    try:
        tls = ctx.wrap_socket(sock, server_hostname=host)
    except (ssl.SSLError, ssl.CertificateError) as e:
        sock.close()
        raise TlsError(f"The matchmaker {host}'s certificate doesn't check out ({getattr(e, 'reason', e)}).")
    _remember(host.lower())
    return tls


def tls_done(host):
    """Call after an asyncio TLS handshake with this host succeeded."""
    _remember(str(host).lower())
