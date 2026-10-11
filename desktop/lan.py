"""Home-network helpers: this computer's address on the network, and finding a matchmaker on it.

Discovery sends one small UDP broadcast ("is there a Retro-Netplay matchmaker here?") to the local network
only; matchmakers answer with their port and computer name (see server.py, Discovery)."""
import ipaddress
import json
import socket
import time

DISCOVER_ASK = b"RETRO-NETPLAY-DISCOVER/1"
DEFAULT_PORT = 7777


def lan_ip():
    """This computer's address on the home network (e.g. 192.168.1.23), or "" if unknown."""
    for probe in ("192.168.0.1", "10.0.0.1", "192.0.2.1"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 9))           # no packet is sent; this only picks the outgoing interface
            ip = s.getsockname()[0]
            if not ip.startswith(("127.", "0.")):
                return ip
        except OSError:
            pass
        finally:
            s.close()
    return ""


def is_loopback(host):
    h = (host or "").strip().lower()
    return h in ("", "localhost", "0.0.0.0", "::1") or h.startswith("127.")


def is_private(host):
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def discover(timeout=1.5, port=DEFAULT_PORT + 2):
    """-> [{"host", "port", "name"}] for matchmakers that answered on this network."""
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.2)
        targets = {"255.255.255.255"}
        ip = lan_ip()
        if ip:
            targets.add(".".join(ip.split(".")[:3]) + ".255")
        targets.add("127.0.0.1")            # a matchmaker on this same computer
        end = time.monotonic() + timeout
        sent = 0
        while time.monotonic() < end:
            if sent < 3 and time.monotonic() > end - timeout + sent * 0.4:
                for t in targets:
                    try:
                        s.sendto(DISCOVER_ASK, (t, port))
                    except OSError:
                        pass
                sent += 1
            try:
                data, addr = s.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            try:
                m = json.loads(data)
            except ValueError:
                continue
            p = m.get("port")
            if m.get("app") == "retro-netplay" and isinstance(p, int) and 0 < p < 65536:
                host = addr[0]
                if is_loopback(host) and ip:
                    host = ip                # show the address other devices would use
                found[(host, p)] = {"host": host, "port": p, "name": str(m.get("name", ""))[:40]}
    finally:
        s.close()
    return sorted(found.values(), key=lambda x: (is_loopback(x["host"]), x["host"]))


def _distro():
    try:
        txt = open("/etc/os-release").read().lower()
    except OSError:
        return ""
    for name in ("cachyos", "endeavouros", "manjaro", "arch", "bazzite", "nobara", "fedora", "ubuntu", "pop", "mint", "debian", "opensuse"):
        if name in txt:
            return name
    return ""


def firewall_help():
    """The one command that lets phones and other computers reach this computer's matchmaker, for this Linux."""
    d = _distro()
    ufw = "sudo ufw allow 7777:7778/tcp && sudo ufw allow 7779/udp"
    fwd = "sudo firewall-cmd --permanent --add-port=7777-7778/tcp --add-port=7779/udp && sudo firewall-cmd --reload"
    if d in ("cachyos", "endeavouros", "manjaro", "arch", "ubuntu", "pop", "mint", "debian"):
        return f"If another device can't find this computer, its firewall is blocking it. Run once in a terminal: {ufw}"
    if d in ("fedora", "nobara", "bazzite", "opensuse"):
        return f"If another device can't find this computer, its firewall is blocking it. Run once in a terminal: {fwd}"
    return ("If another device can't find this computer, its firewall may be blocking it. "
            f"With ufw: {ufw}   With firewalld: {fwd}")


FIREWALL_HELP = firewall_help()


# ----------------------------------------------------------------- your internet (public) address
IPV4 = __import__("re").compile(r"^\d{1,3}(\.\d{1,3}){3}$")
LOOKUP_URL = "https://api.ipify.org"     # only used when the player presses "Look it up online"


def public_ip_from_router(timeout=2.5):
    """Ask your own router (UPnP) for the internet address. Nothing leaves your home network. "" if it won't say."""
    try:
        import portmap
        gw = portmap.discover_upnp(timeout=timeout)
        ip = gw.external_ip() if gw else ""
        return ip if IPV4.match(ip or "") and not is_private(ip) else ""
    except Exception:
        return ""


def public_ip_from_matchmaker(host, port, timeout=4):
    """A matchmaker on the internet tells you the address it sees ("whoami"). "" if it's on your home network."""
    if is_loopback(host) or is_private(host):
        return ""
    try:
        with socket.create_connection((host, int(port)), timeout) as c:
            c.settimeout(timeout)
            c.sendall(b'{"v":1,"op":"whoami"}\n')
            data = c.makefile("r").readline()
        ip = json.loads(data).get("ip", "")
        return ip if IPV4.match(ip) and not is_private(ip) else ""
    except (OSError, ValueError, AttributeError):
        return ""


def public_ip_online(timeout=5):
    """Ask a public "what's my IP" service. Only on request: that service sees your address."""
    import urllib.request
    try:
        with urllib.request.urlopen(LOOKUP_URL, timeout=timeout) as r:
            ip = r.read(64).decode("ascii", "ignore").strip()
        return ip if IPV4.match(ip) else ""
    except Exception:
        return ""


def parse_address(text):
    """'192.168.1.23', '1.2.3.4:7777' or 'Retro-Netplay: connect to 1.2.3.4:7777' -> (host, port) or None."""
    import re
    m = re.search(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(?::(\d{2,5}))?(?![\w.])", text or "")
    if not m:
        return None
    host, port = m.group(1), int(m.group(2) or DEFAULT_PORT)
    if IPV4.match(host) and any(int(x) > 255 for x in host.split(".")):
        return None
    if not 0 < port < 65536 or is_loopback(host):
        return None
    return host, port


def share_text(host, port):
    return f"Retro-Netplay: connect to {host}:{port}"
