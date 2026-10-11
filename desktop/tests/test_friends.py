"""Friends: friends-first queue, wait before strangers, friends-only, priority, mutual adds, privacy of ids,
online list and calls (play / watch), all against the real matchmaker."""
import hashlib, json, os, socket, subprocess, sys, threading, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info else ""), flush=True); ok or fails.append(name)

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

SECRETS = {n: hashlib.sha256(n.encode()).hexdigest()[:32] for n in "ABCDHJSTUVWXYZ"}
def pid(n): return hashlib.sha256(SECRETS[n].encode()).hexdigest()[:24]

port = free_port()
import tempfile
DATA = tempfile.mkdtemp()
def start_server():
    return subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "desktop/server.py"), str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, "NETPLAY_DATA": DATA})
srv = start_server()
time.sleep(1)

class Conn:
    def __init__(self, first):
        self.s = socket.create_connection(("127.0.0.1", port), 5)
        self.f = self.s.makefile("r", encoding="utf-8")
        self.send(first)
        self.msgs, self.lock = [], threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
    def send(self, m): self.s.sendall((json.dumps(m) + "\n").encode())
    def _read(self):
        try:
            for line in self.f:
                m = json.loads(line)
                if m.get("op") != "ping":
                    with self.lock: self.msgs.append(m)
        except Exception: pass
    def wait(self, op, t=4.0, pred=lambda m: True):
        end = time.time() + t
        while time.time() < end:
            with self.lock:
                for m in self.msgs:
                    if m.get("op") == op and pred(m):
                        self.msgs.remove(m); return m
            time.sleep(0.03)
        return None
    def close(self):
        try: self.s.shutdown(socket.SHUT_RDWR); self.s.close()
        except OSError: pass

def search(game, who=None, friends=(), priority=(), wait=None, only=False, name=None, can_host=True, extra=None):
    m = {"v": 1, "op": "search", "game": game, "lanes": ["native"], "can_host": {"native": can_host}, "client": "test"}
    if who:
        m["me"] = SECRETS[who]; m["name"] = name or who
        m["friends"] = [pid(f) for f in friends]; m["priority"] = [pid(f) for f in priority]
        if wait is not None: m["friend_wait"] = wait
        if only: m["friends_only"] = True
    m.update(extra or {})
    return Conn(m)

try:
    # 1. a friend beats a stranger who was already waiting
    s1 = search("gba:one", "S")
    time.sleep(0.2)
    a = search("gba:one", "A", friends=["B"], wait=3)
    time.sleep(0.4)
    check("friend-waiting player isn't matched with a stranger at once", a.wait("matched", 0.3) is None)
    b = search("gba:one", "B", friends=["A"])
    ma, mb = a.wait("matched"), b.wait("matched")
    check("friends are matched with each other", ma and mb and ma["peer"].get("friend") and mb["peer"].get("friend"), (ma, mb))
    check("a friend sees the other's id and name", ma and ma["peer"].get("id") == pid("B") and ma["peer"].get("name") == "B")
    check("the stranger is still waiting", s1.wait("matched", 0.3) is None)
    for c in (a, b, s1): c.close()

    # 2. after the friend wait, strangers are fine; ids are never shown to strangers
    s2 = search("gba:two", "S", name="Stranger")
    a = search("gba:two", "A", friends=["B"], wait=1)
    check("still waiting for friends before the head start ends", a.wait("matched", 0.6) is None)
    ma, ms = a.wait("matched", 2.5), s2.wait("matched", 2.5)
    check("matched with a stranger after the friend wait", ma is not None and ms is not None)
    check("stranger gets name but not id or friend flag", ms and ms["peer"].get("name") == "A" and "id" not in ms["peer"] and "friend" not in ms["peer"], ms)
    for c in (a, s2): c.close()

    # 3. friends only never meets strangers
    s3 = search("gba:three")
    a = search("gba:three", "A", friends=["B"], wait=0, only=True)
    check("friends-only never matches a stranger", a.wait("matched", 1.5) is None and s3.wait("matched", 0.1) is None)
    b = search("gba:three", "B", friends=["A"])
    check("friends-only still matches a friend", a.wait("matched") is not None and b.wait("matched") is not None)
    for c in (a, b, s3): c.close()

    # 4. priority friend wins over a friend who has waited longer
    b = search("gba:four", "B", friends=["A"]); time.sleep(0.2)
    c = search("gba:four", "C", friends=["A"]); time.sleep(0.2)
    a = search("gba:four", "A", friends=["B", "C"], priority=["C"])
    ma = a.wait("matched")
    check("priority friend is picked first", ma and ma["peer"].get("id") == pid("C"), ma)
    check("the other friend keeps waiting", b.wait("matched", 0.3) is None)
    for x in (a, b, c): x.close()

    # 5. friendship must be mutual
    b = search("gba:five", "B")                     # B never added A
    a = search("gba:five", "A", friends=["B"], wait=1)
    check("one-sided friend is not treated as a friend", a.wait("matched", 0.6) is None)
    ma = a.wait("matched", 2.5)
    check("one-sided friend matches only as a stranger", ma and "friend" not in ma["peer"] and "id" not in ma["peer"], ma)
    for x in (a, b): x.close()

    # 6. bad secret refused
    bad = Conn({"v": 1, "op": "search", "game": "gba:x", "lanes": ["native"], "me": "NOT-HEX"})
    check("bad secret is refused", (bad.wait("error") or {}).get("msg", "").startswith("me must"))
    bad.close()

    # 7. prefer_host decides who hosts when both can
    b = search("gba:seven", "B", friends=["A"]); time.sleep(0.2)
    a = search("gba:seven", "A", friends=["B"], extra={"prefer_host": True})
    ma = a.wait("matched")
    check("the player already in the game hosts (prefer_host)", ma and ma["role"] == "host", ma)
    for x in (a, b): x.close()

    # 8. online list and calls
    def hello(n, friends):
        return Conn({"v": 1, "op": "hello", "me": SECRETS[n], "name": n, "friends": [pid(f) for f in friends]})
    pa = hello("A", ["B", "C"])
    check("hello answers with your own id", (pa.wait("hello") or {}).get("id") == pid("A"))
    pa.wait("friends")
    pb = hello("B", ["A"])
    fl = pa.wait("friends", pred=lambda m: m["online"])
    check("A sees B come online", fl and [f["id"] for f in fl["online"]] == [pid("B")], fl)
    pc = hello("C", [])                              # C hasn't added A back
    check("one-sided friends are not shown online", pa.wait("friends", 0.6, pred=lambda m: any(f["id"] == pid("C") for f in m["online"])) is None)
    pb.send({"op": "status", "state": "playing", "game": "gba:sonic-battle"})
    st = pa.wait("friends", pred=lambda m: m["online"] and m["online"][0]["state"] == "playing")
    check("status updates reach friends", st and st["online"][0]["game"] == "gba:sonic-battle", st)
    pa.send({"op": "call", "to": pid("B"), "game": "gba:sonic-battle", "kind": "play", "call": "call0001"})
    cb = pb.wait("call")
    check("call reaches the friend with caller name", cb and cb["from"] == pid("A") and cb["name"] == "A" and cb["kind"] == "play", cb)
    pb.send({"op": "answer", "to": pid("A"), "call": "call0001", "ok": True})
    sa, sb = pa.wait("start"), pb.wait("start")
    check("accepting starts both with the same private code", sa and sb and sa["code"] == sb["code"] and sa["as"] == "caller" and sb["as"] == "callee", (sa, sb))
    if sa and sb:
        x = search("gba:sonic-battle", "A", friends=["B"], extra={"invite": sa["code"], "invite_only": True})
        y = search("gba:sonic-battle", "B", friends=["A"], extra={"invite": sb["code"], "invite_only": True, "prefer_host": True})
        mx, my = x.wait("matched"), y.wait("matched")
        check("the call's private room matches them, callee hosts", mx and my and my["role"] == "host" and mx["via"] == "invite", (mx, my))
        x.close(); y.close()
    pa.send({"op": "call", "to": pid("C"), "game": "gba:sonic-battle", "kind": "watch", "call": "call0002"})
    check("calling a one-sided friend fails", (pa.wait("call_failed") or {}).get("msg") == "not online")
    pa.send({"op": "call", "to": pid("B"), "game": "gba:sonic-battle", "kind": "watch", "call": "call0003"})
    pb.wait("call")
    pb.send({"op": "answer", "to": pid("A"), "call": "call0003", "ok": False})
    check("declining tells the caller", (pa.wait("declined") or {}).get("call") == "call0003")
    pb.send({"op": "answer", "to": pid("A"), "call": "nosuchcall", "ok": True})
    check("answering a call that doesn't exist does nothing", pa.wait("start", 0.5) is None)
    pb.close()
    check("A sees B go offline", pa.wait("friends", pred=lambda m: not m["online"]) is not None)
    for x in (pa, pc): x.close()

    # 8b. short friend codes and requests
    def hello2(n, friends, hidden=False):
        c = Conn({"v": 1, "op": "hello", "me": SECRETS[n], "name": n, "friends": [pid(f) for f in friends], "hidden": hidden})
        return c, c.wait("hello")
    pw, hw = hello2("W", [])
    code_w = hw.get("code", "")
    check("hello gives a short friend code like K7M-4QZ", len(code_w) == 7 and code_w[3] == "-", code_w)
    pw.close(); time.sleep(0.3)
    pw, hw2 = hello2("W", [])
    check("the code stays the same next time", hw2.get("code") == code_w)
    px, hx = hello2("X", [])
    px.send({"op": "lookup", "code": code_w.lower().replace("-", " ")})
    lk = px.wait("lookup")
    check("lookup finds the player by code (any case, spaces)", lk and lk["found"] and lk["id"] == pid("W") and lk["name"] == "W", lk)
    px.send({"op": "lookup", "code": "ZZZ-ZZZ"})
    check("unknown code not found", (px.wait("lookup") or {}).get("found") is False)
    px.send({"op": "friend_request", "to": pid("W")})
    check("sender is told the request went out", px.wait("request_sent") is not None)
    rq = pw.wait("friend_request")
    check("request arrives with name and code", rq and rq["from"] == pid("X") and rq["name"] == "X" and rq["code"] == hx["code"], rq)
    pw.send({"op": "friend_accept", "to": pid("X")})
    ac = px.wait("friend_accept")
    check("acceptance reaches the sender", ac and ac["from"] == pid("W"), ac)
    pw.close(); time.sleep(0.3)
    px.send({"op": "friend_request", "to": pid("W")})
    px.wait("request_sent")
    pw, _ = hello2("W", [])
    check("a request sent while offline arrives when they connect", pw.wait("friend_request") is not None)
    # hidden players can be friends but aren't listed online
    py_, _ = hello2("Y", ["Z"], hidden=True)
    pz, _ = hello2("Z", ["Y"])
    check("hidden friend not listed online", pz.wait("friends", 1.0, pred=lambda m: any(f["id"] == pid("Y") for f in m["online"])) is None)
    py_.close(); pz.close()
    for _ in range(25): px.send({"op": "lookup", "code": "ABCDEF"})
    check("lookups are rate limited (no guessing codes)", px.wait("error_soft", 2) is not None)
    px.close(); pw.close()
    srv.terminate(); srv.wait(); time.sleep(0.3)
    srv = start_server(); time.sleep(1)
    q, _ = hello2("V", [])
    q.send({"op": "lookup", "code": code_w})
    check("codes survive a matchmaker restart", (q.wait("lookup") or {}).get("found") is True)
    q.close()

    # 8c. joining a friend: their abilities are visible; a private room that can't work says so at once
    ph, _ = hello2("H", ["J"]); pj, _ = hello2("J", ["H"])
    ph.send({"op": "status", "state": "playing", "game": "gamecube:f-zero-gx", "can": {"native": False, "stream": False}})
    st = pj.wait("friends", pred=lambda m: m["online"] and m["online"][0].get("state") == "playing")
    check("friends see what the host can host", st and st["online"][0]["can"] == {"native": False, "stream": False}, st)
    host = search("gamecube:f-zero-gx", "H", friends=["J"], extra={"invite": "room1234", "invite_only": True, "lanes": ["native", "stream"],
                  "can_host": {"native": False, "stream": False}})
    time.sleep(0.3)
    joiner = search("gamecube:f-zero-gx", "J", friends=["H"], extra={"invite": "room1234", "invite_only": True, "lanes": ["stream"],
                    "can_host": {"stream": False}})
    ej, eh = joiner.wait("error"), host.wait("error")
    check("two who can't connect are told right away (both)", ej and eh and ej.get("why") == "no_common_lane" and "stream" in ej["msg"], (ej, eh))
    for x in (ph, pj, host, joiner): x.close()
    w = Conn({"v": 1, "op": "whoami"})
    check("whoami tells you the address the matchmaker sees", (w.wait("whoami") or {}).get("ip") == "127.0.0.1")
    w.close()

    # 9. friend link page
    with urllib.request.urlopen(f"http://127.0.0.1:{port + 1}/friend?i={pid('A')}&n=%3Cb%3EAl", timeout=5) as r:
        body = r.read().decode()
    check("friend page links into the app", "netplay://friend?i=" + pid("A") in body)
    check("friend page drops markup from names", "<b>Al" not in body and "bAl" in body)
    with urllib.request.urlopen(f"http://127.0.0.1:{port + 1}/friend?i=zzz&n=x", timeout=5) as r:
        check("damaged friend link explained", b"damaged" in r.read())
    with urllib.request.urlopen(f"http://127.0.0.1:{port + 1}/friend?c=k7m-4qz&n=Sam", timeout=5) as r:
        check("short-code friend link opens the app", "netplay://friend?c=K7M4QZ" in r.read().decode())
finally:
    srv.terminate()

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
