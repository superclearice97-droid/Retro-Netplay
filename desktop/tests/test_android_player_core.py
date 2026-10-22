"""The Android core's pieces for the native player, on a plain JVM against the real matchmaker: the stream connection
(StreamSignal) with a Python host on the other side, the controller packet (exactly what the host reads), the match
and plan for a TCP session (OPEN_PLAYER, never a web page), and encrypted connections (MmTls) with a test certificate."""
import json, os, shutil, socket, subprocess, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.dirname(HERE))
import stream_signal, uinput_pad
ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""), flush=True)
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p
if not shutil.which("javac"):
    print("SKIP (needs javac)"); sys.exit(0)

d = tempfile.mkdtemp()
def sh(*a): subprocess.run(a, cwd=d, check=True, capture_output=True)
sh("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=ca", "-keyout", "ca.key", "-out", "ca.crt")
open(os.path.join(d, "san.ext"), "w").write("subjectAltName=DNS:localhost\n")
sh("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost", "-keyout", "mm.key", "-out", "mm.csr")
sh("openssl", "x509", "-req", "-in", "mm.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-days", "2", "-extfile", "san.ext", "-out", "mm.crt")

port = free_port()
srv = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "desktop/server.py"), str(port), "0"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.STDOUT, env={**os.environ, "NETPLAY_DATA": tempfile.mkdtemp(), "NETPLAY_ICE_JSON": "[]",
                       "NETPLAY_TLS_CERT": os.path.join(d, "mm.crt"), "NETPLAY_TLS_KEY": os.path.join(d, "mm.key")})
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", port), 0.3).close(); break
    except OSError:
        time.sleep(0.1)

prog = r"""
import net.netplay.core.*; import java.util.*; import java.util.concurrent.*; import java.io.*; import java.security.*;
import java.security.cert.*; import javax.net.ssl.*;
public class PJ {
  static void p(String k, Object v) { System.out.println(k + "=" + v); System.out.flush(); }
  public static void main(String[] a) throws Exception {
    int port = Integer.parseInt(a[0]); String mode = a[1];
    if (mode.equals("packets")) {
      StringBuilder hex = new StringBuilder();
      for (byte b : Pad.pack((1 << 0) | (1 << 14), new float[]{0.5f, -1f, 0f, 1f}, 0, 255, 7)) hex.append(String.format("%02x", b));
      p("PACKET", hex);
      StringBuilder hex2 = new StringBuilder();
      for (byte b : Pad.pack(1 << 7, new float[]{3f, -3f, 0f, 0f}, 0, 0, 256)) hex2.append(String.format("%02x", b));
      p("PACKET2", hex2);
      Pad pad = new Pad();
      byte[] x1 = pad.next(0), x2 = pad.next(10); pad.buttons = 1; byte[] x3 = pad.next(20); byte[] x4 = pad.next(80);
      p("BEAT", (x1 != null) + "," + (x2 != null) + "," + (x3 != null) + "," + (x4 != null) + "," + pad.sent);
      pad.ax[0] = 0.05f; pad.buttons = 0; byte[] x5 = pad.next(200);
      p("DEADZONE", x5[6] == 0 && x5[7] == 0);
      return;
    }
    if (mode.equals("guest")) {     // search as the phone, then the stream connection
      Games.Sys ps2 = Games.SYSTEMS.get("ps2");
      Plan.Offer offer = Plan.offer(ps2, false, false, false);
      Map<String, Object> info = new LinkedHashMap<>(); info.put("native", new LinkedHashMap<String, Object>()); info.put("stream", Plan.streamInfo(false));
      p("INFO", Json.stringify(Plan.streamInfo(false)));
      MatchClient.Match m = new MatchClient().search("127.0.0.1", port, Games.key("ps2", "Urban Reign"), offer.lanes, info, offer.canHost, "android");
      p("SIG", m.sig);
      Plan plan = Plan.make(ps2, m.lane, m.role, m.peer, "tcp".equals(m.sig));
      p("PLAN", plan.kind);
      BlockingQueue<Map<String, Object>> got = new LinkedBlockingQueue<>(); BlockingQueue<String> end = new LinkedBlockingQueue<>();
      StreamSignal sig = new StreamSignal("127.0.0.1", port, m.sessionKey, new StreamSignal.Listener() {
        public void onMessage(Map<String, Object> msg) { got.add(msg); }
        public void onEnd(String why) { end.add(why); } });
      Map<String, Object> cfg = sig.connect();
      p("ROLE", cfg.get("role")); p("ICE", Json.stringify(cfg.get("iceServers")));
      sig.start();
      Map<String, Object> hello = new LinkedHashMap<>(); hello.put("type", "hello"); sig.send(hello);
      Map<String, Object> offerMsg = got.poll(10, TimeUnit.SECONDS); p("GOT", offerMsg == null ? "" : Json.stringify(offerMsg));
      Map<String, Object> ans = new LinkedHashMap<>(); ans.put("type", "answer"); ans.put("sdp", "v=0 answer"); sig.send(ans);
      Map<String, Object> bye = got.poll(10, TimeUnit.SECONDS); p("BYE", bye == null ? "" : Json.stringify(bye));
      p("END", end.poll(10, TimeUnit.SECONDS));
      try { new StreamSignal("127.0.0.1", port, "not-a-real-key-at-all", null).connect(); p("BADKEY", "accepted"); }
      catch (StreamSignal.Failure f) { p("BADKEY", f.why); }
      return;
    }
    if (mode.equals("words")) {      // what the player says when the stream stops (one line each, JSON-quoted)
      for (String w : new String[]{"session closed", "session ended", "lost contact with the matchmaker", "ice failed", "no host", "no video", "other words"}) {
        p("END_" + w.replace(' ', '_') + "_0", Json.stringify(PlayerText.end(w, false)));
        p("END_" + w.replace(' ', '_') + "_1", Json.stringify(PlayerText.end(w, true)));
      }
      p("BYE_none", Json.stringify(PlayerText.bye(null)));
      p("BYE_why", Json.stringify(PlayerText.bye("the game window was closed.")));
      return;
    }
    if (mode.equals("tls")) {
      CertificateFactory cf = CertificateFactory.getInstance("X.509");
      KeyStore ks = KeyStore.getInstance(KeyStore.getDefaultType()); ks.load(null, null);
      ks.setCertificateEntry("ca", cf.generateCertificate(new FileInputStream(a[2])));
      TrustManagerFactory tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()); tmf.init(ks);
      SSLContext ctx = SSLContext.getInstance("TLS"); ctx.init(null, tmf.getTrustManagers(), null);
      p("RULE_NAMES", MmTls.wantsTls("mm.example.net") + "," + MmTls.wantsTls("192.168.1.2") + "," + MmTls.wantsTls("localhost") + "," + MmTls.wantsTls("nas.lan"));
      MmTls.rule = h -> h.equals("localhost");
      try { MmTls.connect("localhost", port, 5000); p("UNTRUSTED", "accepted"); } catch (MmTls.TlsException e) { p("UNTRUSTED", "refused"); }
      MmTls.factory = ctx.getSocketFactory();
      java.net.Socket s = MmTls.connect("localhost", port, 5000);
      p("TLS", s instanceof SSLSocket ? ((SSLSocket) s).getSession().getProtocol() : "plain");
      s.getOutputStream().write("{\"v\":1,\"op\":\"whoami\"}\n".getBytes("UTF-8"));
      p("WHOAMI", new BufferedReader(new InputStreamReader(s.getInputStream(), "UTF-8")).readLine());
      p("SEEN", MmTls.seen.has("localhost"));
      try { MmTls.connect("localhost", Integer.parseInt(a[3]), 5000); p("DOWNGRADE", "accepted"); } catch (MmTls.TlsException e) { p("DOWNGRADE", "refused"); }
      MmTls.rule = h -> true;
      try { MmTls.connect("127.0.0.1", port, 5000); p("WRONGNAME", "accepted"); } catch (MmTls.TlsException e) { p("WRONGNAME", "refused"); }
      return;
    }
  }
}
"""
out = tempfile.mkdtemp(); src = os.path.join(out, "PJ.java"); open(src, "w").write(prog)
core = os.path.join(ROOT, "android/core/src/main/java/net/netplay/core")
r = subprocess.run(["javac", "-d", out] + [os.path.join(core, f) for f in os.listdir(core)] + [src], capture_output=True, text=True)
check("Android core compiles", r.returncode == 0, r.stderr[-1500:])
def java(*args, timeout=40):
    r = subprocess.run(["java", "-cp", out, "PJ", str(port), *args], capture_output=True, text=True, timeout=timeout)
    return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l), r
try:
    kv, r = java("packets")
    p = uinput_pad.parse_packet(bytes.fromhex(kv.get("PACKET", "")))
    check("packet: the host reads exactly what the phone packed", p == {"seq": 7, "buttons": 16385, "ax": (16384, -32767, 0, 32767), "lt": 0, "rt": 255}, p)
    p2 = uinput_pad.parse_packet(bytes.fromhex(kv.get("PACKET2", "")))
    check("packet: seq wraps, sticks clamp, the right-trigger button means a full trigger", p2 and p2["seq"] == 0 and p2["ax"][:2] == (32767, -32767) and p2["rt"] == 255, p2)
    check("packet: sent on change, heartbeat every 50 ms", kv.get("BEAT") == "true,false,true,true,3", kv.get("BEAT"))
    check("packet: small stick moves are the dead zone", kv.get("DEADZONE") == "true")

    import stream_guest
    kv, r = java("words")
    same = [w for w in ("session closed", "session ended", "lost contact with the matchmaker", "ice failed", "no host", "no video", "other words")
            for relay in (0, 1) if json.loads(kv.get(f"END_{w.replace(' ', '_')}_{relay}", "null")) != stream_guest.end_text(w, bool(relay))]
    check("the phone's player says the same as the desktop player when a stream stops", not same and kv, same or r.stderr[-800:])
    check("...and shows the host's goodbye the same way", json.loads(kv.get("BYE_none", "null")) == stream_guest.bye_text(None)
          and json.loads(kv.get("BYE_why", "null")) == stream_guest.bye_text("the game window was closed."), kv.get("BYE_why"))

    # ---- the phone joins a desktop host: match, plan, stream connection
    h = socket.create_connection(("127.0.0.1", port))
    h.sendall((json.dumps({"v": 1, "op": "search", "game": "ps2:urban-reign", "lanes": ["stream"], "client": "desktop-linux",
                           "can_host": {"stream": True}, "info": {"stream": {"backend": "own", "sig": "tcp"}}}) + "\n").encode())
    time.sleep(0.3)
    res = {}
    t = threading.Thread(target=lambda: res.update(zip(("kv", "r"), java("guest"))), daemon=True); t.start()
    h.settimeout(15); buf = b""
    while not buf.endswith(b"\n"): buf += h.recv(4096)
    mh = json.loads(buf)
    A = stream_signal.TcpSignaling("127.0.0.1", port, mh["session"]["key"], on_msg=lambda m: seen.append(m))
    seen = []
    A.connect(); A.start()
    for _ in range(100):
        if seen: break
        time.sleep(0.1)
    A.send({"type": "offer", "sdp": "v=0 offer"})
    for _ in range(100):
        if len(seen) >= 2: break
        time.sleep(0.1)
    A.send({"type": "bye", "why": "the game window was closed"}); A.flush(); time.sleep(0.5)
    t.join(30); kv = res.get("kv", {})
    check("the phone says it signals over TCP", json.loads(kv.get("INFO", "{}")).get("sig") == "tcp", kv.get("INFO"))
    check("its match is a TCP session", kv.get("SIG") == "tcp", kv)
    check("the plan opens the native player", kv.get("PLAN") == "OPEN_PLAYER", kv.get("PLAN"))
    check("the stream connection gives it the guest role and ICE servers", kv.get("ROLE") == "guest" and kv.get("ICE", "").startswith("["), kv)
    check("phone -> host: hello", seen and seen[0] == {"type": "hello"}, seen)
    check("host -> phone: the offer", json.loads(kv.get("GOT") or "{}") == {"type": "offer", "sdp": "v=0 offer"}, kv.get("GOT"))
    check("phone -> host: the answer", len(seen) > 1 and seen[1] == {"type": "answer", "sdp": "v=0 answer"}, seen)
    check("the host's goodbye arrives with its reason, then the end", json.loads(kv.get("BYE") or "{}").get("why") == "the game window was closed"
          and kv.get("END") == "session closed", kv)
    check("a wrong key is refused", kv.get("BADKEY") == "bad_key", kv.get("BADKEY"))
    A.stop()

    # ---- encrypted connections
    plain_port = free_port()
    plain = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "desktop/server.py"), str(plain_port), "0"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.STDOUT, env={**os.environ, "NETPLAY_DATA": tempfile.mkdtemp()})
    time.sleep(1.0)
    kv, r = java("tls", os.path.join(d, "ca.crt"), str(plain_port))
    plain.terminate()
    check("TLS rule: internet names yes; addresses, localhost, home names no", kv.get("RULE_NAMES") == "true,false,false,false", kv.get("RULE_NAMES"))
    check("a certificate the phone can't verify is refused", kv.get("UNTRUSTED") == "refused", r.stdout + r.stderr[-500:])
    check("with a trusted certificate the connection is TLS", kv.get("TLS", "").startswith("TLSv1."), r.stdout[-800:] + r.stderr[-1500:])
    check("...and the matchmaker answers over it", '"op": "whoami"' in kv.get("WHOAMI", ""), kv.get("WHOAMI"))
    check("the matchmaker is remembered as encrypted", kv.get("SEEN") == "true")
    check("the same name without TLS is then refused", kv.get("DOWNGRADE") == "refused", kv.get("DOWNGRADE"))
    check("a trusted certificate for a different name is refused", kv.get("WRONGNAME") == "refused", kv.get("WRONGNAME"))
finally:
    srv.terminate()
print("ALL PASSED" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
