"""The phone's engine (android/core Arcade + LocalApi, plain Java) against the real lobby: the same interface API as
the desktop (state, events, actions), a phone sees a computer's stream room, asks to join, is let in and its player
opens with a working stream key; lobby chat both ways; a netplay room without the game says what's missing; the
phone says it can't host yet; the API needs its token and refuses other sites."""
import json, os, re, secrets, socket, subprocess, sys, tempfile, threading, time, urllib.request, urllib.error, asyncio
HERE = os.path.dirname(os.path.abspath(__file__)); D = os.path.dirname(HERE); sys.path.insert(0, D)
os.environ["NETPLAY_DATA"] = tempfile.mkdtemp()
import server, sessions

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{str(info)[:700]}]" if info and not ok else ""), flush=True); ok or fails.append(name)

PORT = 7960
threading.Thread(target=lambda: asyncio.run(server.main(PORT, 0, discovery=False)), daemon=True).start()
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", PORT), 0.2).close(); break
    except OSError:
        time.sleep(0.05)

class Host:
    """A computer hosting rooms (speaks the lobby protocol directly)."""
    def __init__(self):
        self.s = socket.create_connection(("127.0.0.1", PORT), 3); self.buf = b""; self.got = []
        self.s.sendall((json.dumps({"op": "lobby", "v": 2, "me": secrets.token_hex(16), "name": "PC Host"}) + "\n").encode())
        self.wait("welcome")
    def send(self, m): self.s.sendall((json.dumps(m) + "\n").encode())
    def wait(self, op, timeout=8):
        for i, m in enumerate(self.got):
            if m.get("op") == op: return self.got.pop(i)
        end = time.time() + timeout
        while time.time() < end:
            if b"\n" not in self.buf:
                self.s.settimeout(max(0.05, end - time.time()))
                try: chunk = self.s.recv(65536)
                except (socket.timeout, OSError): continue
                if not chunk: break
                self.buf += chunk; continue
            line, self.buf = self.buf.split(b"\n", 1)
            m = json.loads(line)
            if m.get("op") == op: return m
            self.got.append(m)
        return {}

ROOT = os.path.join(os.path.dirname(D), "android", "core", "src", "main", "java")
UI = os.path.join(D, "ui")
MAIN = r'''
import net.netplay.core.*;
import java.util.*;
public class PhoneCheck {
  public static void main(String[] a) throws Exception {
    System.setOut(new java.io.PrintStream(System.out, true, "UTF-8"));
    final Map<String, Object> saved = new LinkedHashMap<>();
    final List<Map<String, Object>> lib = new ArrayList<>();
    Map<String, Object> g = new LinkedHashMap<>();
    g.put("i", 0); g.put("name", "Sonic Battle"); g.put("system", "gba"); g.put("key", "gba:sonic-battle"); g.put("problem", "");
    g.put("screens", new ArrayList<>()); g.put("box", "");
    lib.add(g);
    Arcade.Platform pf = new Arcade.Platform() {
      public Map<String, Object> loadSettings() { Map<String, Object> m = new LinkedHashMap<>(); m.put("lobby", a[0]); return m; }
      public void saveSettings(Map<String, Object> s) { saved.clear(); saved.putAll(s); }
      public List<Map<String, Object>> library() { return lib; }
      public void openPlayer(String host, int port, String key, String hostName, boolean relay) {
        System.out.println("OPEN_PLAYER " + host + " " + port + " " + key + " " + hostName); }
      public String openNetplay(Map<String, Object> game, Map<String, Object> nat) {
        System.out.println("OPEN_NETPLAY " + game.get("name") + " " + Json.stringify(nat)); return "RetroArch is connecting."; }
      public void stopGames() { System.out.println("STOP_GAMES"); }
      public List<String> findLobbies() { return new ArrayList<>(); }
    };
    Arcade arc = new Arcade(pf);
    arc.listen((name, data) -> { if ("notice".equals(name)) System.out.println("NOTICE " + Json.stringify(data)); });
    LocalApi api = new LocalApi(arc, path -> {
      try { return java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(a[1], path)); } catch (Exception e) { return null; } },
      path -> null);
    api.start();
    arc.start();
    System.out.println("PHONE_UI " + api.url());
    Thread.sleep(120000);
  }
}
'''
out = tempfile.mkdtemp()
open(os.path.join(out, "PhoneCheck.java"), "w").write(MAIN)
subprocess.run(["javac", "-encoding", "UTF-8", "-d", out, "-sourcepath", ROOT, os.path.join(out, "PhoneCheck.java")], check=True, capture_output=True)
phone = subprocess.Popen(["java", "-cp", out, "PhoneCheck", f"127.0.0.1:{PORT}", UI], stdout=subprocess.PIPE, text=True)
lines = []
def reader():
    for l in phone.stdout:
        lines.append(l.strip())
threading.Thread(target=reader, daemon=True).start()
end = time.time() + 20
while time.time() < end and not any(l.startswith("PHONE_UI") for l in lines):
    time.sleep(0.1)
url = next(l.split()[1] for l in lines if l.startswith("PHONE_UI"))
m = re.match(r"(http://127\.0\.0\.1:\d+)/#t=(\w+)", url)
BASE, TOK = m.group(1), m.group(2)

def get(path, token=True, host=None):
    req = urllib.request.Request(BASE + path, headers={"X-Token": TOK} if token else {})
    if host: req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, b""
def state(): return json.loads(get("/api/state")[1])
def do(action, **kw):
    req = urllib.request.Request(BASE + "/api/do", data=json.dumps(dict(kw, action=action)).encode(), headers={"X-Token": TOK})
    with urllib.request.urlopen(req, timeout=8) as r: return json.loads(r.read())
def until(cond, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        s = state()
        if cond(s): return s
        time.sleep(0.2)
    return state()

try:
    check("the phone serves the same interface page", get("/", token=False)[0] == 200 and b"app.js" in get("/", token=False)[1])
    check("its API needs the token", get("/api/state", token=False)[0] == 403)
    check("another site can't use it (Host header)", get("/api/state", host="evil.example")[0] == 403)
    check("no files outside the interface", get("/../engine.py", token=False)[0] == 404)
    s = state()
    check("first start: setup not done, a player id made", not s["setup"]["done"] and re.fullmatch(r"[0-9a-f]{24}", s["me"]["id"]), s["setup"])
    check("13+ is required", not do("setup", name="Bea", age_ok=False)["ok"])
    do("setup", name="Bea", age_ok=True)
    s = until(lambda s: s["lobby"]["connected"] and s["setup"]["done"])
    check("set up, and connected to the lobby", s["lobby"]["connected"] and s["me"]["name"] == "Bea", s["lobby"])
    check("the phone says it can't host yet", s["device"] == {"phone": True, "can_host": False} and "phone" in do("post", game=0)["msg"])

    pc = Host()
    pc.send({"op": "post", "room": {"game": "ps2:urban-reign", "title": "Urban Reign", "system": "ps2", "mode": "stream", "seats": 2,
                                    "note": "Story co-op", "auto": False, "public": True}})
    room = pc.wait("posted")["room"]
    s = until(lambda s: s["rooms"])
    check("the phone sees the computer's room", s["rooms"] and s["rooms"][0]["title"] == "Urban Reign", s["rooms"])
    do("join", room=room["id"])
    req = pc.wait("request")
    check("the host is asked", req.get("player", {}).get("name") == "Bea", req)
    s = until(lambda s: s["seat"] and s["seat"].get("waiting"))
    check("the phone shows it's waiting", s["seat"] and s["seat"]["waiting"], s["seat"])
    pc.send({"op": "answer", "req": req["req"], "accept": True})
    seated = pc.wait("seated")
    s = until(lambda s: s["seat"] and s["seat"].get("seat") == 2)
    time.sleep(0.3)
    opened = [l for l in lines if l.startswith("OPEN_PLAYER")]
    key = opened[0].split()[3] if opened else ""
    found = sessions.lookup(key)
    check("let in: Player 2, and the player opens with the guest key of the host's session",
          s["seat"]["seat"] == 2 and found and found[1] == "guest" and sessions.lookup(seated["session"]["key"])[0] is found[0], (opened, s["seat"]))
    check("the player is pointed at the lobby with the host's name", opened and opened[0].split()[1:3] == ["127.0.0.1", str(PORT)] and "PC Host" in opened[0], opened)

    do("chat", to="lobby", text="hi from the phone")
    m = pc.wait("chat")
    check("lobby chat from the phone", m.get("text") == "hi from the phone" and m.get("from", {}).get("name") == "Bea", m)
    pc.send({"op": "chat", "to": room["id"], "text": "welcome!"})
    s = until(lambda s: s["chat"]["room"])
    check("room chat reaches the phone", s["chat"]["room"] and s["chat"]["room"][-1]["text"] == "welcome!", s["chat"])
    do("block", player=room["host"]["id"], name="PC Host")
    check("blocking is saved", until(lambda s: s["blocked"])["blocked"][0]["name"] == "PC Host")
    do("unblock", player=room["host"]["id"])

    pc.send({"op": "close"})
    s = until(lambda s: not s["seat"])
    check("the host closed the room: the phone leaves it and stops the game", not s["seat"] and any(l == "STOP_GAMES" for l in lines), s["seat"])

    pc.send({"op": "post", "room": {"game": "gba:bomberman-tournament", "title": "Bomberman Tournament", "system": "gba", "mode": "netplay",
                                    "seats": 2, "auto": True, "native": {"emu": "retroarch", "port": 55435}}})
    bomber = pc.wait("posted")["room"]
    until(lambda s: any(r["id"] == bomber["id"] for r in s["rooms"]))
    do("join", room=bomber["id"])
    s = until(lambda s: "own copy" in s["playing"]["note"])
    check("a netplay room without the game: the phone says what's missing", "own copy of Bomberman Tournament" in s["playing"]["note"], (s["playing"], [l for l in lines if l.startswith("NOTICE")][-3:]))
finally:
    phone.terminate()

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
