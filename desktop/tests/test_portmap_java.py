"""The Android core's PortMap against the same pretend router and the real matchmaker."""
import os, socket, subprocess, sys, tempfile, threading, time, http.server
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import importlib.util
spec = importlib.util.spec_from_file_location("tp", os.path.join(HERE, "test_portmap.py"))
src = open(os.path.join(HERE, "test_portmap.py")).read().split("gw = portmap.discover_upnp")[0]   # reuse the pretend router only
ns = {"__file__": os.path.join(HERE, "test_portmap.py")}
exec(compile(src, "router", "exec"), ns)
loc, state = ns["loc"], ns["state"]
fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info else "")); ok or fails.append(name)

mm = 47823
p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(HERE), "server.py"), str(mm)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
root = os.path.join(os.path.dirname(os.path.dirname(HERE)), "android", "core", "src", "main", "java")
try:
    for _ in range(50):
        try: socket.create_connection(("127.0.0.1", mm), timeout=0.2).close(); break
        except OSError: time.sleep(0.1)
    free = socket.socket(); free.bind(("", 0)); tp = free.getsockname()[1]; free.close()
    with tempfile.TemporaryDirectory() as out:
        f = os.path.join(out, "PM.java")
        open(f, "w").write(f"""import net.netplay.core.PortMap; import java.util.Arrays;
public class PM {{ public static void main(String[] a) {{
  PortMap.Gateway g = PortMap.discover(500, Arrays.asList("{loc}"));
  System.out.println("gw=" + (g == null ? "none" : g.name + "|" + g.controlUrl));
  System.out.println("ext=" + PortMap.externalIp(g));
  PortMap.Result r = PortMap.open(55435, "TCP", "", Arrays.asList("{loc}"));
  System.out.println("open=" + r.ok + "|" + r.method);
  PortMap.delete(g, 55435, "TCP");
  System.out.println("far=" + (PortMap.discover(300, Arrays.asList("http://8.8.8.8/x.xml")) == null));
  PortMap.Check c = PortMap.check("127.0.0.1", {mm}, {tp});
  System.out.println("check=" + c.open + "|" + c.ip);
}} }}""")
        subprocess.run(["javac", "-d", out, "-sourcepath", root, f], check=True, stderr=subprocess.DEVNULL)
        before = dict(state["maps"])
        outp = subprocess.run(["java", "-cp", out, "PM"], capture_output=True, text=True, timeout=60).stdout
    lines = dict(l.split("=", 1) for l in outp.strip().splitlines())
    check("Java finds the router", lines.get("gw", "").startswith("Pretend Router|"), lines.get("gw"))
    check("Java reads public address", lines.get("ext") == "203.0.113.7", lines.get("ext"))
    check("Java opens the port with UPnP", lines.get("open") == "true|UPnP", lines.get("open"))
    check("Java removed the mapping", ("55435", "TCP") not in state["maps"] and "AddPortMapping" in state["calls"] and "DeletePortMapping" in state["calls"])
    check("Java refuses non-local routers", lines.get("far") == "true")
    check("Java port check through the matchmaker", lines.get("check") == "true|127.0.0.1", lines.get("check"))
finally:
    p.terminate()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
