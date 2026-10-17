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


_HOME_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10")]


def is_private(host):
    """A home-network address (same ranges as Lan.isPrivate on the phone)."""
    try:
        a = ipaddress.ip_address(str(host).strip())
    except ValueError:
        return False
    return a.version == 4 and any(a in n for n in _HOME_NETS)


def _entry(host, m, fallback_port=DEFAULT_PORT):
    p = m.get("port", fallback_port)
    if m.get("app") != "retro-netplay" or not isinstance(p, int) or not 0 < p < 65536:
        return None
    since = m.get("since")
    return {"host": host, "port": p, "name": str(m.get("name", ""))[:40],
            "since": since if isinstance(since, int) else None, "id": str(m.get("id", ""))[:32]}


def _merge(found, e):
    """One entry per matchmaker (the same one can answer from two addresses, e.g. cable and Wi-Fi)."""
    key = e["id"] or f"{e['host']}:{e['port']}"
    old = found.get(key)
    if old is None or (is_loopback(old["host"]) and not is_loopback(e["host"])):
        found[key] = e


def discover(timeout=1.5, port=DEFAULT_PORT + 2):
    """-> [{"host", "port", "name", "since", "id"}] for matchmakers that answered on this network."""
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
            e = _entry(addr[0], m) if isinstance(m, dict) else None
            if e:
                if is_loopback(e["host"]) and ip:
                    e["host"] = ip           # show the address other devices would use
                _merge(found, e)
    finally:
        s.close()
    return sorted(found.values(), key=lambda x: (is_loopback(x["host"]), x["host"]))


def probe(host, port=DEFAULT_PORT, timeout=0.4):
    """Ask one address "are you a Retro-Netplay matchmaker?" over TCP. -> entry or None."""
    try:
        with socket.create_connection((host, int(port)), timeout) as c:
            c.settimeout(1.0)
            c.sendall(b'{"v":1,"op":"whoami"}\n')
            m = json.loads(c.makefile("r").readline() or "{}")
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict) or m.get("op") != "whoami":
        return None
    m.setdefault("app", "retro-netplay")      # older matchmakers answer whoami without saying who they are
    return _entry(host, dict(m, port=int(port)))


def sweep(port=DEFAULT_PORT, timeout=0.4):
    """When a router drops broadcasts: knock on every address of this home network (x.x.x.1 - .254) directly."""
    ip = lan_ip()
    if not ip or not is_private(ip):
        return []
    base = ".".join(ip.split(".")[:3])
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(64) as ex:
        res = list(ex.map(lambda n: probe(f"{base}.{n}", port, timeout), range(1, 255)))
    found = {}
    for e in res:
        if e:
            _merge(found, e)
    return list(found.values())


def pick(found):
    """The matchmaker everyone on this network should use: running longest, then the lowest address.
    Phones apply the same rule (Lan.pick), so everybody lands on the same one."""
    if not found:
        return None
    def key(e):
        try:
            addr = tuple(int(x) for x in e["host"].split("."))
        except ValueError:
            addr = (999,)
        return (e["since"] if e.get("since") is not None else 1 << 62, addr)
    return min(found, key=key)


def find_all(port=DEFAULT_PORT):
    """Broadcast first; if nothing answers, try every address on the network."""
    return discover(port=port + 2) or sweep(port)


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
