"""Automatic port opening against a pretend router, and the matchmaker's connect-back port check."""
import http.server, os, socket, subprocess, sys, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import portmap

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info else "")); ok or fails.append(name)

DESC = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><friendlyName>Pretend Router</friendlyName>
<deviceList><device><deviceList><device><serviceList><service>
<serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType><controlURL>/ctl/IPConn</controlURL>
</service></serviceList></device></deviceList></device></deviceList></device></root>"""
state = {"maps": {}, "permanent_only": False, "calls": []}

class Router(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/xml"); self.end_headers(); self.wfile.write(DESC.encode())
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        action = self.headers["SOAPAction"].split("#")[1].strip('"')
        state["calls"].append(action)
        get = lambda k: body.split(f"<{k}>")[1].split(f"</{k}>")[0]
        if action == "AddPortMapping":
            if state["permanent_only"] and get("NewLeaseDuration") != "0":
                return self.fault(725)
            key = (get("NewExternalPort"), get("NewProtocol"))
            if key in state["maps"] and state["maps"][key] != get("NewInternalClient"):
                return self.fault(718)
            state["maps"][key] = get("NewInternalClient")
            return self.ok("")
        if action == "DeletePortMapping":
            state["maps"].pop((get("NewExternalPort"), get("NewProtocol")), None); return self.ok("")
        if action == "GetExternalIPAddress":
            return self.ok("<NewExternalIPAddress>203.0.113.7</NewExternalIPAddress>")
    def ok(self, inner):
        self.send_response(200); self.end_headers()
        self.wfile.write(f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><u:R xmlns:u="urn:x">{inner}</u:R></s:Body></s:Envelope>'.encode())
    def fault(self, code):
        self.send_response(500); self.end_headers()
        self.wfile.write(f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0"><errorCode>{code}</errorCode></UPnPError></detail></s:Fault></s:Body></s:Envelope>'.encode())

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Router)
threading.Thread(target=srv.serve_forever, daemon=True).start()
loc = f"http://127.0.0.1:{srv.server_port}/desc.xml"

gw = portmap.discover_upnp(locations=[loc])
check("finds the router's control point", gw is not None and gw.control_url.endswith("/ctl/IPConn"), gw and gw.control_url)
check("reads router name", gw and gw.name == "Pretend Router")
check("public address", gw and gw.external_ip() == "203.0.113.7")
check("refuses non-local routers", portmap.discover_upnp(locations=["http://8.8.8.8/desc.xml"]) is None)

pm = portmap.PortMapper(locations=[loc])
r = pm.open(55435, "TCP")
check("opens the port", r["ok"] and r["method"] == "UPnP" and ("55435", "TCP") in state["maps"], r)
pm.close_all()
check("closes it on exit", ("55435", "TCP") not in state["maps"])
state["permanent_only"] = True
pm2 = portmap.PortMapper(locations=[loc])
r = pm2.open(2626, "UDP")
check("falls back to a permanent mapping when leases aren't allowed", r["ok"] and ("2626", "UDP") in state["maps"])
pm2.close_all()
check("permanent mapping still removed on exit", ("2626", "UDP") not in state["maps"])
state["maps"][("55435", "TCP")] = "10.9.9.9"
r = portmap.PortMapper(locations=[loc]).open(55435, "TCP")
check("explains a port already used by another device", not r["ok"] and "already using" in r["message"], r["message"][:90])
r = portmap.PortMapper(locations=["http://127.0.0.1:9/none"]).open(55435, "TCP")
check("no router: plain-language fallback", not r["ok"] and "still join" in r["message"])

# ---- connect-back check through the real matchmaker
mm = 47811
p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(HERE), "server.py"), str(mm)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try: socket.create_connection(("127.0.0.1", mm), timeout=0.2).close(); break
        except OSError: time.sleep(0.1)
    free = socket.socket(); free.bind(("", 0)); test_port = free.getsockname()[1]; free.close()
    res = portmap.check_port("127.0.0.1", mm, test_port)
    check("port check: reachable port reported open", res["open"] is True and res["ip"] == "127.0.0.1", res)
    other = socket.socket(); other.bind(("", 0)); other.listen(1); busy = other.getsockname()[1]
    res = portmap.check_port("127.0.0.1", mm, busy)
    check("port check: busy port explained", res["open"] is None and "in use" in res["message"])
    other.close()
    # a different program answering on the port must not count as open
    impostor = socket.socket(); impostor.bind(("", 0)); impostor.listen(1); ip_port = impostor.getsockname()[1]
    def imp():
        c, _ = impostor.accept(); c.sendall(b"hello\n"); c.close()
    threading.Thread(target=imp, daemon=True).start()
    s = socket.create_connection(("127.0.0.1", mm)); s.sendall(b'{"v":1,"op":"probe","port":%d,"token":"abcdef0123456789"}\n' % ip_port)
    import json
    ans = json.loads(s.makefile().readline()); s.close(); impostor.close()
    check("port check: only our app's answer counts", ans.get("open") is False, ans)
    s = socket.create_connection(("127.0.0.1", mm)); s.sendall(b'{"v":1,"op":"probe","port":22,"token":"abcdef0123456789"}\n')
    ans = json.loads(s.makefile().readline()); s.close()
    check("port check: system ports refused", ans.get("op") == "error", ans)
finally:
    p.terminate()

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
