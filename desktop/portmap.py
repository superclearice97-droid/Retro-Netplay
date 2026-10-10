"""Open a port on the home router automatically, so hosting works without router settings.

Tries UPnP (most home routers), then NAT-PMP (Apple and some others). Only ever talks to devices on your
own network, only opens the ports the app needs, renews them while the app runs and closes them on exit.
check_port() then asks the matchmaker to try connecting back, which proves the port really is reachable.

Pure standard library. Nothing here sends data anywhere except your router and the matchmaker you chose.
"""
import ipaddress
import json
import secrets
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

SSDP_ADDR = ("239.255.255.250", 1900)
SEARCH_TARGETS = ["urn:schemas-upnp-org:device:InternetGatewayDevice:1",
                  "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
                  "urn:schemas-upnp-org:service:WANIPConnection:1"]
WAN_SERVICES = ["urn:schemas-upnp-org:service:WANIPConnection:2", "urn:schemas-upnp-org:service:WANIPConnection:1",
                "urn:schemas-upnp-org:service:WANPPPConnection:1"]
MAX_XML = 256 * 1024
LEASE = 3600
DESCRIPTION = "Retro-Netplay"

# The ports the app may open, and why. Nothing else is ever mapped.
APP_PORTS = {
    "retroarch": (55435, "TCP", "RetroArch netplay"),
    "dolphin": (2626, "UDP", "Dolphin netplay"),
}


def _private(host):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_link_local


def _local_ip_towards(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


class Gateway:
    def __init__(self, control_url, service, local_ip, name=""):
        self.control_url, self.service, self.local_ip, self.name = control_url, service, local_ip, name

    def _soap(self, action, args, timeout=5):
        body = "".join(f"<{k}>{v}</{k}>" for k, v in args)
        env = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
               's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
               f'<u:{action} xmlns:u="{self.service}">{body}</u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(self.control_url, data=env.encode(), method="POST", headers={
            "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{self.service}#{action}"'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # the router is local: never via a proxy
        try:
            with opener.open(req, timeout=timeout) as r:
                return 200, r.read(MAX_XML).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read(MAX_XML).decode("utf-8", "replace")

    @staticmethod
    def _error_code(text):
        try:
            for el in ET.fromstring(text).iter():
                if el.tag.endswith("errorCode"):
                    return int(el.text)
        except (ET.ParseError, ValueError, TypeError):
            pass
        return None

    def add(self, port, proto, lease=LEASE):
        args = [("NewRemoteHost", ""), ("NewExternalPort", port), ("NewProtocol", proto), ("NewInternalPort", port),
                ("NewInternalClient", self.local_ip), ("NewEnabled", 1), ("NewPortMappingDescription", DESCRIPTION),
                ("NewLeaseDuration", lease)]
        code, text = self._soap("AddPortMapping", args)
        if code == 200:
            return True, lease, ""
        err = self._error_code(text)
        if err == 725 and lease:          # router only allows permanent mappings: we delete it ourselves on exit
            return self.add(port, proto, 0)
        if err == 718:
            return False, lease, "another device on your network is already using this port"
        return False, lease, f"router refused (error {err or code})"

    def delete(self, port, proto):
        self._soap("DeletePortMapping", [("NewRemoteHost", ""), ("NewExternalPort", port), ("NewProtocol", proto)], timeout=3)

    def external_ip(self):
        code, text = self._soap("GetExternalIPAddress", [])
        if code != 200:
            return ""
        try:
            for el in ET.fromstring(text).iter():
                if el.tag.endswith("NewExternalIPAddress"):
                    return (el.text or "").strip()
        except ET.ParseError:
            pass
        return ""


def parse_description(xml_text, location):
    """-> (control_url, service_type, friendly_name) for the first WAN connection service, or None."""
    root = ET.fromstring(xml_text)
    ns = lambda el: el.tag.rsplit("}", 1)[-1]
    base = location
    for el in root.iter():
        if ns(el) == "URLBase" and (el.text or "").strip():
            base = el.text.strip()
    name = ""
    for el in root.iter():
        if ns(el) == "friendlyName":
            name = (el.text or "").strip()
            break
    found = {}
    for svc in root.iter():
        if ns(svc) != "service":
            continue
        fields = {ns(c): (c.text or "").strip() for c in svc}
        if fields.get("serviceType") in WAN_SERVICES and fields.get("controlURL"):
            found[fields["serviceType"]] = urllib.parse.urljoin(base, fields["controlURL"])
    for st in WAN_SERVICES:
        if st in found:
            return found[st], st, name
    return None


def discover_upnp(timeout=2.5, locations=None):
    """Find the router's UPnP control point. locations: skip discovery (used by tests)."""
    if locations is None:
        locations = []
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(0.4)
        try:
            for st in SEARCH_TARGETS:
                msg = (f"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: {st}\r\n\r\n")
                s.sendto(msg.encode(), SSDP_ADDR)
            end = time.time() + timeout
            while time.time() < end:
                try:
                    data, addr = s.recvfrom(4096)
                except socket.timeout:
                    continue
                for line in data.decode("utf-8", "replace").split("\r\n"):
                    if line.lower().startswith("location:"):
                        loc = line.split(":", 1)[1].strip()
                        if loc not in locations:
                            locations.append(loc)
        except OSError:
            pass
        finally:
            s.close()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for loc in locations:
        host = urllib.parse.urlsplit(loc).hostname or ""
        if not _private(host):
            continue           # only ever talk to devices on the home network
        try:
            with opener.open(loc, timeout=3) as r:
                text = r.read(MAX_XML).decode("utf-8", "replace")
            got = parse_description(text, loc)
        except Exception:
            continue
        if got:
            control, st, name = got
            if not _private(urllib.parse.urlsplit(control).hostname or ""):
                continue
            return Gateway(control, st, _local_ip_towards(host), name)
    return None


# ------------------------------------------------------------------ NAT-PMP (RFC 6886)
def default_gateway():
    """IPv4 default gateway, read without running any program. Linux only; returns '' elsewhere."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                p = line.split()
                if len(p) > 2 and p[1] == "00000000" and int(p[3], 16) & 2:
                    return socket.inet_ntoa(struct.pack("<L", int(p[2], 16)))
    except OSError:
        pass
    return ""


def natpmp_map(gateway, port, proto, lease=LEASE, timeout=1.0):
    if not gateway or not _private(gateway):
        return False, ""
    op = 2 if proto == "TCP" else 1
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(struct.pack("!BBHHHI", 0, op, 0, port, port, lease), (gateway, 5351))
        data, addr = s.recvfrom(64)
        if addr[0] != gateway or len(data) < 16:
            return False, ""
        ver, rop, result, _epoch, _iport, eport, _life = struct.unpack("!BBHIHHI", data[:16])
        return result == 0 and rop == 128 + op and eport == port, ""
    except OSError:
        return False, ""
    finally:
        s.close()


# ------------------------------------------------------------------ mapping manager
class PortMapper:
    """Keeps the app's ports open while it runs. Thread-safe; every call is quick to give up."""

    def __init__(self, locations=None):
        self.lock = threading.Lock()
        self.gw = None
        self.method = ""
        self.mapped = {}        # (port, proto) -> lease seconds
        self.locations = locations
        self._renewer = None
        self.last = {}          # (port, proto) -> result dict, for the UI

    def open(self, port, proto="TCP"):
        """-> {"ok": bool, "method": "UPnP"|"NAT-PMP"|"", "router": name, "message": plain words}"""
        with self.lock:
            if self.gw is None and self.method != "NAT-PMP":
                self.gw = discover_upnp(locations=self.locations)
            if self.gw:
                ok, lease, why = self.gw.add(port, proto)
                if ok:
                    self.method = "UPnP"
                    self.mapped[(port, proto)] = lease
                    self._start_renewer()
                    res = {"ok": True, "method": "UPnP", "router": self.gw.name,
                           "message": f"Your router opened port {port} automatically."}
                    self.last[(port, proto)] = res
                    return res
                fail = why
            else:
                fail = ""
            gw = default_gateway()
            ok, _ = natpmp_map(gw, port, proto)
            if ok:
                self.method = "NAT-PMP"
                self.mapped[(port, proto)] = LEASE
                self._start_renewer()
                res = {"ok": True, "method": "NAT-PMP", "router": gw, "message": f"Your router opened port {port} automatically."}
            else:
                res = {"ok": False, "method": "", "router": "",
                       "message": ("Your router didn't allow automatic setup" + (f" ({fail})" if fail else "") +
                                   ". You can still join other people's games and streams. To host, turn on UPnP in your "
                                   f"router's settings, or forward port {port} {proto} to this device.")}
            self.last[(port, proto)] = res
            return res

    def _start_renewer(self):
        if self._renewer and self._renewer.is_alive():
            return
        self._renewer = threading.Thread(target=self._renew, daemon=True)
        self._renewer.start()

    def _renew(self):
        while True:
            time.sleep(LEASE / 2)
            with self.lock:
                items = [(k, v) for k, v in self.mapped.items() if v]
            for (port, proto), _lease in items:
                try:
                    if self.method == "UPnP" and self.gw:
                        self.gw.add(port, proto)
                    elif self.method == "NAT-PMP":
                        natpmp_map(default_gateway(), port, proto)
                except Exception:
                    pass

    def close_all(self):
        with self.lock:
            for (port, proto) in list(self.mapped):
                try:
                    if self.method == "UPnP" and self.gw:
                        self.gw.delete(port, proto)
                    elif self.method == "NAT-PMP":
                        natpmp_map(default_gateway(), port, proto, lease=0)
                except Exception:
                    pass
            self.mapped.clear()


# ------------------------------------------------------------------ reachability check through the matchmaker
def check_port(server_host, server_port, port, timeout=8):
    """Listen on `port` for a moment and ask the matchmaker to connect back.
    -> {"open": True/False/None, "ip": public address or "", "message": plain words}. None = couldn't test."""
    token = secrets.token_hex(12)
    try:
        lst = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lst.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        lst.bind(("", port))
        lst.listen(2)
    except OSError:
        return {"open": None, "ip": "", "message": f"Port {port} is in use by another program right now, so it can't be tested."}
    lst.settimeout(timeout)
    stop = threading.Event()

    def answer():
        while not stop.is_set():
            try:
                c, _ = lst.accept()
            except OSError:
                return
            try:
                c.sendall((token + "\n").encode())
            finally:
                c.close()
    t = threading.Thread(target=answer, daemon=True)
    t.start()
    try:
        with socket.create_connection((server_host, int(server_port)), timeout=6) as s:
            s.sendall((json.dumps({"v": 1, "op": "probe", "port": port, "token": token}) + "\n").encode())
            s.settimeout(timeout)
            line = s.makefile("rb").readline(4096)
        msg = json.loads(line or b"{}")
    except (OSError, ValueError):
        return {"open": None, "ip": "", "message": "Couldn't reach the matchmaker to test the port."}
    finally:
        stop.set()
        lst.close()
    if msg.get("op") == "error":
        return {"open": None, "ip": "", "message": "The matchmaker can't test ports: " + str(msg.get("msg", ""))[:120]}
    ok = bool(msg.get("open"))
    ip = str(msg.get("ip", ""))[:64]
    return {"open": ok, "ip": ip, "message": (f"Port {port} is open. Other players can connect to you." if ok else
                                              f"Port {port} is still closed from the internet. You can join others, but not host native netplay.")}
