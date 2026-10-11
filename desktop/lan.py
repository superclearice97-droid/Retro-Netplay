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


FIREWALL_HELP = ("If another device can't find this computer, its firewall may be blocking the matchmaker. "
                 "Fedora/Nobara: sudo firewall-cmd --permanent --add-port=7777-7778/tcp --add-port=7779/udp && "
                 "sudo firewall-cmd --reload.  Ubuntu/Mint: sudo ufw allow 7777:7778/tcp && sudo ufw allow 7779/udp.")
