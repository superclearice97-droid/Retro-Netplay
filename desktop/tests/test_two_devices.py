"""Two devices at home: the desktop app runs the matchmaker; the phone (Android core) finds it on the network,
the two become friends by short code, and the desktop's invite link points at a real address (never 127.0.0.1)."""
import asyncio, os, socket, subprocess, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["NETPLAY_DATA"] = tempfile.mkdtemp()
import lan, invites, social, server

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info else ""), flush=True); ok or fails.append(name)

def free_port():
    while True:
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            u.bind(("0.0.0.0", p + 2)); u.close(); return p
        except OSError:
            u.close()

P = free_port()
threading.Thread(target=lambda: asyncio.run(server.main(P)), daemon=True).start()
time.sleep(0.8)

# the desktop app's settings when it runs the matchmaker itself
cfg = {"invite": {"host": ""}, "server_host": "127.0.0.1", "server_port": P, "run_server": True}
host = invites.link_host(cfg)
check("links made on the matchmaker computer never say 127.0.0.1", host and not lan.is_loopback(host), host)
link = invites.build_link(cfg, "gba:sonic-battle", "ABCD2345")
check("invite link uses the home-network address", f"http://{host}:{P + 1}/join?" in link, link)

found = lan.discover(1.2, P + 2)
check("desktop 'Find on my network' sees the matchmaker", any(f["port"] == P for f in found), found)

# desktop side: presence through the in-app matchmaker
dsk_secret = social.new_secret()
events = []
dp = social.Presence("127.0.0.1", P, dsk_secret, "Desk", [], lambda ev, d: events.append((ev, d)))
dp.start()
def wait(pred, t=5):
    end = time.time() + t
    while time.time() < end:
        for e in list(events):
            if pred(e): return e
        time.sleep(0.05)
    return None
up = wait(lambda e: e[0] == "up")
desk_code = up[1]["code"] if up else ""
check("desktop gets a short friend code", len(desk_code) == 7, desk_code)

# phone side (Android core): discover, then add the desktop by its code
root = os.path.join(ROOT, "android", "core", "src", "main", "java")
phone_secret = "d" * 32
prog = f"""
import net.netplay.core.*; import java.util.*; import java.util.concurrent.*;
public class TD {{
  public static void main(String[] a) throws Exception {{
    List<Lan.Found> f = Lan.discover(Arrays.asList("255.255.255.255", "127.0.0.1"), 1500, {P + 2});
    String host = f.isEmpty() ? "" : f.get(0).host; int port = f.isEmpty() ? 0 : f.get(0).port;
    System.out.println("found=" + host + ":" + port);
    System.out.println("loop=" + Lan.isLoopback("127.0.0.1") + Lan.isLoopback("localhost") + Lan.isLoopback("192.168.1.5"));
    final BlockingQueue<String> ev = new LinkedBlockingQueue<>();
    final String[] deskId = {{""}};
    Presence p = new Presence("127.0.0.1", port, "{phone_secret}", "Odin", new ArrayList<String>(), new Presence.Listener() {{
      public void onConnected(boolean c, String w) {{ if (c) ev.add("up"); }}
      public void onOnline(List<Map<String, Object>> o) {{ ev.add("online " + o.size()); }}
      public void onCall(String a1, String b, String c, String d, String e) {{ }}
      public void onStart(String a1, String b, String c, String d, String e, String f1, String g) {{ }}
      public void onDeclined(String a1, String b) {{ }}
      public void onCallFailed(String a1, String b) {{ }}
      public void onCallCancelled(String a1, String b) {{ }}
      public void onLookup(String code, boolean found, String id, String name, boolean self) {{ deskId[0] = id; ev.add("lookup " + found + " " + name); }}
      public void onFriendAccepted(String id, String name, String code) {{ ev.add("accepted " + name); }}
    }});
    p.start();
    System.out.println("up=" + ev.poll(5, TimeUnit.SECONDS));
    p.lookup("{desk_code}");
    String l; do {{ l = ev.poll(5, TimeUnit.SECONDS); }} while (l != null && !l.startsWith("lookup"));
    System.out.println("lookup=" + l);
    p.request(deskId[0]);
    do {{ l = ev.poll(8, TimeUnit.SECONDS); }} while (l != null && !l.startsWith("accepted"));
    System.out.println("accepted=" + l);
    Invite inv = Invite.parse("{link}");
    System.out.println("invite=" + (inv == null ? "null" : inv.host + ":" + inv.port + " loop=" + Lan.isLoopback(inv.host)));
    p.stop();
  }}
}}"""
with tempfile.TemporaryDirectory() as out:
    open(os.path.join(out, "TD.java"), "w").write(prog)
    subprocess.run(["javac", "-d", out, "-sourcepath", root, os.path.join(out, "TD.java")], check=True, stderr=subprocess.DEVNULL)
    proc = subprocess.Popen(["java", "-cp", out, "TD"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    req = wait(lambda e: e[0] == "friend_request", 12)
    check("desktop receives the phone's friend request", req and req[1]["name"] == "Odin", req)
    if req:
        dp.accept(req[1]["id"])
    so, _ = proc.communicate(timeout=60)
res = dict(l.split("=", 1) for l in so.strip().splitlines() if "=" in l)
check("phone 'Find on my network' finds the matchmaker", res.get("found", "").endswith(f":{P}"), res.get("found"))
check("phone treats 127.0.0.1/localhost as 'not set'", res.get("loop") == "truetruefalse")
check("phone connects", res.get("up") == "up", res.get("up"))
check("phone finds the desktop by its short code", res.get("lookup") == "lookup true Desk", res.get("lookup"))
check("desktop accepted: phone is told", res.get("accepted") == "accepted Desk", res.get("accepted"))
check("phone reads the desktop's invite link with a real address", res.get("invite", "").startswith(f"{host}:{P} loop=false"), res.get("invite"))
dp.stop()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
