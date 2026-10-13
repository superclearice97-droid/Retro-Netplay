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
      public void onCall(String a1, String b, String c, String d) {{ }}
      public void onStart(String a1, String b, String c, String e, String f1, String g) {{ }}
      public void onDeclined(String a1, String b, String c) {{ }}
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

# the phone asks to join the desktop, which is playing (and can stream); the desktop accepts on its card
import json as _j
dp2_events = []
dp2 = social.Presence("127.0.0.1", P, dsk_secret, "Desk", [server.player_id(phone_secret)], lambda ev, d: dp2_events.append((ev, d)))
dp.stop(); time.sleep(0.3); dp2.start(); time.sleep(0.6)
dp2.status("playing", "ps2:urban-reign", {"native": False, "stream": True})
prog2 = f"""
import net.netplay.core.*; import java.util.*; import java.util.concurrent.*;
public class TJ {{
  public static void main(String[] a) throws Exception {{
    final BlockingQueue<String> ev = new LinkedBlockingQueue<>();
    final String[] code = {{""}};
    final Map<String, Object> seen = new HashMap<>();
    Presence p = new Presence("127.0.0.1", {P}, "{phone_secret}", "Odin", Arrays.asList("{social.id_of(dsk_secret)}"), new Presence.Listener() {{
      public void onConnected(boolean c, String w) {{ if (c) ev.add("up"); }}
      public void onOnline(List<Map<String, Object>> o) {{ if (!o.isEmpty()) {{ seen.putAll(o.get(0)); ev.add("online " + o.get(0).get("state") + " " + o.get(0).get("can")); }} }}
      public void onCall(String a1, String b, String c, String d) {{ }}
      public void onStart(String id, String c, String g, String as, String wid, String wn) {{ code[0] = c; ev.add("start " + as + " " + wn); }}
      public void onDeclined(String a1, String b, String c) {{ ev.add("declined"); }}
      public void onCallFailed(String a1, String b) {{ ev.add("failed " + b); }}
      public void onCallCancelled(String a1, String b) {{ }}
    }});
    p.start();
    String l; do {{ l = ev.poll(6, TimeUnit.SECONDS); }} while (l != null && !l.startsWith("online playing"));
    System.out.println("seen=" + l);
    p.call("{social.id_of(dsk_secret)}", "ps2:urban-reign");
    do {{ l = ev.poll(8, TimeUnit.SECONDS); }} while (l != null && !l.startsWith("start"));
    System.out.println("start=" + l);
    Map<String, Boolean> ch = new HashMap<>(); ch.put("stream", false);
    Map<String, Object> info = new HashMap<>(); info.put("stream", Plan.streamInfo(false));
    MatchClient.Match m = new MatchClient().search("127.0.0.1", {P}, "ps2:urban-reign", Arrays.asList("stream"), info, ch, "android",
        code[0], true, Friends.searchFields("{phone_secret}", "Odin", false));
    System.out.println("match=" + m.lane + " " + m.role + " " + (m.play.startsWith("/play?t=")));
    p.stop();
  }}
}}"""
with tempfile.TemporaryDirectory() as out:
    open(os.path.join(out, "TJ.java"), "w").write(prog2)
    subprocess.run(["javac", "-d", out, "-sourcepath", root, os.path.join(out, "TJ.java")], check=True, stderr=subprocess.DEVNULL)
    proc = subprocess.Popen(["java", "-cp", out, "TJ"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    end = time.time() + 15
    call = None
    while time.time() < end and not call:
        call = next((d for ev, d in dp2_events if ev == "call"), None); time.sleep(0.05)
    check("desktop gets the phone's join request", call and call["name"] == "Odin" and call["game"] == "ps2:urban-reign", call)
    if call:
        dp2.answer(call["from"], call["call"], True)            # the host taps Accept on the corner card
    st = None
    while time.time() < end and not st:
        st = next((d for ev, d in dp2_events if ev == "start"), None); time.sleep(0.05)
    msg = None
    if st:   # the desktop (host, already playing) searches in the call's room, able to host a stream
        c = socket.create_connection(("127.0.0.1", P), 5)
        c.sendall((_j.dumps({"v": 1, "op": "search", "game": "ps2:urban-reign", "lanes": ["stream"], "invite": st["code"],
                              "invite_only": True, "can_host": {"stream": True}, "info": {"stream": {"backend": "own"}},
                              "client": "desktop-linux", **social.search_fields({"secret": dsk_secret, "my_name": "Desk"},
                              prefer_host=True)}) + "\n").encode())
        f = c.makefile("r")
        while True:
            line = f.readline()
            if not line or _j.loads(line).get("op") != "ping":
                msg = _j.loads(line) if line else None; break
        c.close()
    so, _ = proc.communicate(timeout=40)
r2 = dict(l.split("=", 1) for l in so.strip().splitlines() if "=" in l)
check("phone sees the desktop is playing and can stream", "online playing" in r2.get("seen", "") and "stream=true" in r2.get("seen", ""), r2.get("seen"))
check("after Accept both start", r2.get("start") == "start caller Desk", r2.get("start"))
check("desktop hosts the stream", msg and msg.get("role") == "host" and msg.get("lane") == "stream", msg)
check("phone gets the stream page to open", r2.get("match") == "stream guest true", r2.get("match"))
dp2.stop()

# emulator netplay with the host on the matchmaker's own computer: the phone must get the PC's Wi-Fi address,
# not 127.0.0.1 (which would be the phone itself)
home = lan.lan_ip()
if home:
    def search(addr, can_host, prefer):
        c = socket.create_connection((addr, P), 5)
        c.sendall((_j.dumps({"v": 1, "op": "search", "game": "gba:sonic-battle", "lanes": ["native"], "invite": "HOMETEST1",
                             "invite_only": True, "can_host": {"native": can_host}, "client": "x", "prefer_host": prefer}) + "\n").encode())
        return c
    def matched(c):
        f = c.makefile("r")
        while True:
            line = f.readline()
            if not line or _j.loads(line).get("op") != "ping":
                return _j.loads(line) if line else {}
    pc = search("127.0.0.1", True, True)          # the desktop app talks to its own matchmaker at 127.0.0.1
    time.sleep(0.2)
    phone = search(home, False, False)           # the phone uses the PC's Wi-Fi address
    mp, mpc = matched(phone), matched(pc)
    check("phone joins the PC hosting next to the matchmaker at the PC's Wi-Fi address", mp.get("role") == "guest"
          and mp.get("peer", {}).get("ip") == home and mpc.get("role") == "host", (mp, home))
    pc.close(); phone.close()
else:
    print("SKIP home-network address check (no network)")
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
