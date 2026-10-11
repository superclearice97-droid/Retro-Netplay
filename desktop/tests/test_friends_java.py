"""The Android core's friends code (ids, links, online list, calls, friend matching) against the real matchmaker,
and RetroCmd against a pretend RetroArch listening for network commands."""
import hashlib, os, socket, subprocess, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.dirname(HERE))
fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info else ""), flush=True); ok or fails.append(name)
def free_port(kind=socket.SOCK_STREAM):
    s = socket.socket(socket.AF_INET, kind); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

mm = free_port()
import tempfile as _t
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "desktop/server.py"), str(mm)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env={**os.environ, "NETPLAY_DATA": _t.mkdtemp()})

# pretend RetroArch (network commands)
ra_port = free_port(socket.SOCK_DGRAM)
got = []
ra = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); ra.bind(("127.0.0.1", ra_port))
def retroarch():
    while True:
        try: data, addr = ra.recvfrom(4096)
        except OSError: return
        cmd = data.decode()
        got.append(cmd)
        if cmd == "VERSION": ra.sendto(b"1.19.1\n", addr)
        elif cmd == "GET_STATUS": ra.sendto(b"GET_STATUS PLAYING game_boy_advance,Legend of Zelda, The - Minish Cap (USA),crc32=deadbeef\n", addr)
        elif cmd == "GET_CONFIG_PARAM netplay_nickname": ra.sendto(b"GET_CONFIG_PARAM netplay_nickname OdinSam\n", addr)
threading.Thread(target=retroarch, daemon=True).start()

A, B = "a" * 32, "b" * 32
ida, idb = (hashlib.sha256(x.encode()).hexdigest()[:24] for x in (A, B))
src_root = os.path.join(ROOT, "android", "core", "src", "main", "java")
prog = r"""
import net.netplay.core.*; import java.util.*; import java.util.concurrent.*;
public class FJ {
  static void p(String k, Object v) { System.out.println(k + "=" + v); }
  static class L implements Presence.Listener {
    final String who; final BlockingQueue<String> ev = new LinkedBlockingQueue<>(); Presence me;
    L(String w) { who = w; }
    public void onConnected(boolean c, String why) { if (c) ev.add("up"); }
    public void onOnline(List<Map<String, Object>> o) { StringBuilder b = new StringBuilder(); for (Map<String,Object> m : o) b.append(m.get("name")).append(':').append(m.get("state")).append(','); ev.add("online " + b); }
    public void onCall(String from, String name, String game, String kind, String id) { ev.add("call " + name + " " + game + " " + kind); me.answer(from, id, true); }
    public void onStart(String id, String code, String game, String kind, String as, String wid, String wname) { ev.add("start " + code + " " + as + " " + wname); }
    public void onDeclined(String id, String n) { ev.add("declined"); }
    public void onCallFailed(String id, String msg) { ev.add("failed " + msg); }
    public void onCallCancelled(String id, String from) { ev.add("cancelled"); }
    public void onMyCode(String c) { ev.add("code " + c); }
    public void onLookup(String c, boolean found, String id, String name, boolean self) { ev.add("lookup " + found + " " + name + " " + self); }
    public void onFriendRequest(String id, String name, String code) { ev.add("request " + name + " " + code); }
    public void onFriendAccepted(String id, String name, String code) { ev.add("accepted " + name + " " + code); }
    String next(String prefix) throws Exception { long end = System.currentTimeMillis() + 5000; while (System.currentTimeMillis() < end) { String e = ev.poll(200, TimeUnit.MILLISECONDS); if (e != null && e.startsWith(prefix)) return e; } return "none"; }
  }
  public static void main(String[] a) throws Exception {
    RetroCmd.port = %RA%;
    p("idA", Friends.idOf("%A%"));
    p("pretty", Friends.pretty("k7m 4qz") + "|" + Friends.pretty("K7M40Z") + "|" + Friends.shortCode("k7m-4qz"));
    Friends.Friend sc = Friends.parse(" k7m-4qz ");
    p("short", sc == null ? "null" : sc.id + "|" + sc.lookup);
    Friends.Friend sl = Friends.parse("http://1.2.3.4:7778/friend?c=K7M4QZ&n=Bea");
    p("shortLink", sl == null ? "null" : sl.lookup + "|" + sl.name);
    Friends.Friend f1 = Friends.parse("http://203.0.113.5:7778/friend?i=%IDB%&n=Sam%20%3Cb%3E");
    p("webLink", f1 == null ? "null" : f1.id + "|" + f1.name);
    Friends.Friend f2 = Friends.parse("netplay://friend?i=%IDB%&n=Al&host=x&port=7777");
    p("appLink", f2 == null ? "null" : f2.id + "|" + f2.name);
    Friends.Friend f3 = Friends.parse(" %IDB% ".toUpperCase());
    p("typed", f3 == null ? "null" : f3.id);
    p("junk", Friends.parse("http://x/join?i=%IDB%") == null && Friends.parse("abc") == null && Friends.parse("netplay://friend?i=zz") == null);
    p("link", Friends.link("203.0.113.5", 7777, "K7M-4QZ", "Sam B"));
    List<Friends.Friend> l = new ArrayList<>(); Friends.Friend fb = new Friends.Friend("%IDB%", "Bea"); fb.priority = true; fb.join = true; fb.code = "K7M-4QZ"; fb.pending = true; l.add(fb);
    List<Friends.Friend> back = Friends.load(Friends.save(l) );
    p("roundtrip", back.size() == 1 && back.get(0).priority && back.get(0).join && !back.get(0).watch && back.get(0).name.equals("Bea")
        && back.get(0).code.equals("K7M-4QZ") && back.get(0).pending);
    p("badload", Friends.load("[{\"id\":\"nope\"}, 5, \"x\"]").size() + "|" + Friends.load("garbage").size());

    L la = new L("A"), lb = new L("B");
    Presence pa = new Presence("127.0.0.1", %MM%, "%A%", "Ann", Arrays.asList("%IDB%"), la); la.me = pa;
    Presence pb = new Presence("127.0.0.1", %MM%, "%B%", "Bea", Arrays.asList("%IDA%"), lb); lb.me = pb;
    pa.start(); String codeA = la.next("code"); p("aUp", la.next("up")); pb.start(); String codeB = lb.next("code"); p("bUp", lb.next("up"));
    p("aSeesB", la.next("online Bea"));
    p("codes", codeA + "|" + codeB);
    pa.lookup(codeB.substring(5).toLowerCase());
    p("lookup", la.next("lookup"));
    pa.lookup("ZZZ-ZZZ"); p("lookupMiss", la.next("lookup"));
    pa.request("%IDB%"); p("request", lb.next("request"));
    pb.accept("%IDA%"); p("accepted", la.next("accepted"));
    p("codeB", codeB.substring(5));
    pb.status("playing", "gba:sonic-battle");
    p("aSeesPlaying", la.next("online Bea:playing"));
    String cid = pa.call("%IDB%", "gba:sonic-battle", "play");
    p("bGetsCall", lb.next("call"));
    String sa = la.next("start"), sb = lb.next("start");
    p("starts", sa + " / " + sb);
    String code = sa.split(" ")[1];
    final MatchClient.Match[] mA = new MatchClient.Match[1];
    Thread t = new Thread(() -> { try {
        Map<String, Boolean> ch = new HashMap<>(); ch.put("native", true);
        mA[0] = new MatchClient().search("127.0.0.1", %MM%, "gba:sonic-battle", Arrays.asList("native"), new HashMap<String, Object>(), ch, "android", code, true,
            Friends.searchFields("%A%", "Ann", Arrays.asList(new Friends.Friend("%IDB%", "Bea")), false, 15, false));
    } catch (Exception e) { p("errA", e); } });
    t.start(); Thread.sleep(300);
    Map<String, Boolean> ch = new HashMap<>(); ch.put("native", true);
    Map<String, Object> info = new HashMap<>(); Map<String, Object> nat = new HashMap<>(); nat.put("nick", RetroCmd.nickname()); info.put("native", nat);
    MatchClient.Match mB = new MatchClient().search("127.0.0.1", %MM%, "gba:sonic-battle", Arrays.asList("native"), info, ch, "android", code, true,
        Friends.searchFields("%B%", "Bea", Arrays.asList(new Friends.Friend("%IDA%", "Ann")), false, 15, true));
    t.join(5000);
    p("roleB", mB.role + "|" + mB.via);
    p("aSeesFriend", mA[0] == null ? "null" : mA[0].peerString("name") + "|" + mA[0].peerString("friend") + "|" + mA[0].peerString("nick"));
    p("failedCall", la.next("x").equals("none") ? "" : "");
    pa.call("0123456789abcdef01234567", "gba:x", "watch");
    p("strangerCall", la.next("failed"));
    pb.stop();
    p("bOffline", la.next("online "));
    pa.stop();

    RetroCmd.port = %RA%;
    p("raUp", RetroCmd.available());
    RetroCmd.Status s = RetroCmd.status();
    p("raStatus", s.playing + "|" + s.system + "|" + s.basename + "|" + s.crc);
    p("raClean", Library.cleanName(s.basename));
    RetroCmd.showMsg("In the queue\nfor Sonic");
    RetroCmd.hostToggle();
    p("paused", RetroCmd.parseStatus("GET_STATUS PAUSED gba,X,crc32=1").paused + "|" + RetroCmd.parseStatus("GET_STATUS CONTENTLESS").hasGame() + "|" + (RetroCmd.parseStatus("junk") == null));
    RetroCmd.port = 9; p("raDown", RetroCmd.available());
    System.exit(0);
  }
}
"""
for k, v in {"%A%": A, "%B%": B, "%IDA%": ida, "%IDB%": idb, "%MM%": str(mm), "%RA%": str(ra_port)}.items():
    prog = prog.replace(k, v)
try:
    for _ in range(50):
        try: socket.create_connection(("127.0.0.1", mm), timeout=0.2).close(); break
        except OSError: time.sleep(0.1)
    with tempfile.TemporaryDirectory() as out:
        open(os.path.join(out, "FJ.java"), "w").write(prog)
        subprocess.run(["javac", "-d", out, "-sourcepath", src_root, os.path.join(out, "FJ.java")], check=True, stderr=subprocess.DEVNULL)
        r = subprocess.run(["java", "-cp", out, "FJ"], capture_output=True, text=True, timeout=90)
    res = dict(l.split("=", 1) for l in r.stdout.strip().splitlines() if "=" in l)
    if r.returncode: print(r.stdout, r.stderr)
    check("Java id matches the server's hash", res.get("idA") == ida)
    check("friend codes read any way, look-alike letters refused", res.get("pretty") == "K7M-4QZ||K7M4QZ", res.get("pretty"))
    check("typed short code needs a lookup", res.get("short") == "|K7M4QZ", res.get("short"))
    check("short-code friend link", res.get("shortLink") == "K7M4QZ|Bea")
    check("each player gets a short code from the matchmaker", all(len(c) == 12 and c.startswith("code ") and c[8] == "-" for c in res.get("codes", "x|x").split("|")), res.get("codes"))
    check("lookup by code finds the friend", res.get("lookup") == "lookup true Bea false", res.get("lookup"))
    check("unknown code not found", res.get("lookupMiss", "").startswith("lookup false"))
    check("friend request arrives with name and code", res.get("request") == "request Ann " + res.get("codes", "")[5:12], res.get("request"))
    check("acceptance comes back", res.get("accepted") == "accepted Bea " + res.get("codeB", ""), res.get("accepted"))
    check("web friend link parsed, markup dropped", res.get("webLink") == idb + "|Sam b", res.get("webLink"))
    check("app friend link parsed", res.get("appLink") == idb + "|Al")
    check("older long codes still accepted", res.get("typed") == idb)
    check("junk is not a friend", res.get("junk") == "true")
    check("friend link uses the short code", res.get("link") == "http://203.0.113.5:7778/friend?c=K7M4QZ&n=Sam+B", res.get("link"))
    check("friends and privileges save and load", res.get("roundtrip") == "true")
    check("damaged friend lists load as empty", res.get("badload") == "0|0")
    check("both come online", res.get("aUp") == "up" and res.get("bUp") == "up")
    check("A sees B online", res.get("aSeesB", "").startswith("online Bea"))
    check("A sees what B is playing", res.get("aSeesPlaying", "").startswith("online Bea:playing"))
    check("B gets A's call", res.get("bGetsCall") == "call Ann gba:sonic-battle play", res.get("bGetsCall"))
    check("accepted call starts both", res.get("starts", "").startswith("start ") and " caller Bea" in res.get("starts", "") and " callee Ann" in res.get("starts", ""), res.get("starts"))
    check("callee (already in game) hosts the friend match", res.get("roleB") == "host|invite", res.get("roleB"))
    check("A sees B as a friend, with B's RetroArch nickname", res.get("aSeesFriend") == "Bea|true|OdinSam", res.get("aSeesFriend"))
    check("calling a non-friend fails", res.get("strangerCall") == "failed not online", res.get("strangerCall"))
    check("A sees B go offline", res.get("bOffline", "").startswith("online"))
    check("RetroArch answers", res.get("raUp") == "true")
    check("game read from RetroArch, commas in names kept", res.get("raStatus") == "true|game_boy_advance|Legend of Zelda, The - Minish Cap (USA)|deadbeef", res.get("raStatus"))
    check("running game gets the library's name", res.get("raClean") == "The Legend of Zelda - Minish Cap", res.get("raClean"))
    time.sleep(0.3)
    check("on-screen message sent on one line", "SHOW_MSG In the queue for Sonic" in got, got)
    check("host toggle sent", "NETPLAY_HOST_TOGGLE" in got)
    check("status parsing edge cases", res.get("paused") == "true|false|true")
    check("RetroArch off is detected", res.get("raDown") == "false")
finally:
    srv.terminate(); ra.close()
print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
