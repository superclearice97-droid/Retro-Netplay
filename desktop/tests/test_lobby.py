"""The arcade lobby: a host posts a room, everyone sees it, players ask to join and the host accepts (or lets them
straight in), each seat gets its own stream keys (or the host's emulator details for netplay rooms), full rooms say
so, the host can remove and ban, rooms close with their host; lobby and room chat, blocking, the chat limit,
reports, and moderation only for the lobby's own app (which holds the private admin token)."""
import asyncio, json, os, secrets, socket, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
data = tempfile.mkdtemp(); os.environ["NETPLAY_DATA"] = data
with open(os.path.join(data, "lobby-words.txt"), "w") as f:
    f.write("# masked in chat\nbadword\n")
import server, lobby, sessions

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{str(info)[:600]}]" if info and not ok else ""), flush=True); ok or fails.append(name)

PORT = 7930
threading.Thread(target=lambda: asyncio.run(server.main(PORT, 0, discovery=False)), daemon=True).start()
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", PORT), 0.2).close(); break
    except OSError:
        time.sleep(0.05)
ADMIN = open(os.path.join(data, "lobby-admin.token")).read().strip()


class P:
    """One player's lobby connection."""
    def __init__(self, name, admin=False, blocked=()):
        self.secret = secrets.token_hex(16)
        self.s = socket.create_connection(("127.0.0.1", PORT), 3)
        self.buf = b""
        hello = {"op": "lobby", "v": 2, "me": self.secret, "name": name, "client": "test", "blocked": list(blocked)}
        if admin:
            hello["admin"] = ADMIN
        self.send(hello)
        self.got = []
        self.welcome = self.wait("welcome")
        self.id = self.welcome.get("id")

    def send(self, m):
        self.s.sendall((json.dumps(m) + "\n").encode())

    def wait(self, op, timeout=3, cond=lambda m: True):
        for i, m in enumerate(self.got):
            if m.get("op") == op and cond(m):
                return self.got.pop(i)
        end = time.time() + timeout
        while time.time() < end:
            if b"\n" not in self.buf:
                self.s.settimeout(max(0.05, end - time.time()))
                try:
                    chunk = self.s.recv(65536)
                except (socket.timeout, OSError):
                    continue
                if not chunk:
                    break
                self.buf += chunk
                continue
            line, self.buf = self.buf.split(b"\n", 1)
            m = json.loads(line)
            if m.get("op") == op and cond(m):
                return m
            self.got.append(m)
        return {}

    def none(self, op, timeout=0.6):
        return not self.wait(op, timeout)

    def close(self):
        self.s.close()


ROOM = {"game": "gamecube:f-zero-gx", "title": "F-Zero GX", "system": "gamecube", "mode": "stream", "seats": 3,
        "note": "Grand Prix, all welcome", "auto": False, "public": True}

host, sam, bea = P("Host"), P("Sam"), P("Bea")
check("welcome: lobby name, rules, no admin without the token", host.welcome.get("lobby", {}).get("rules") and host.welcome.get("admin") is False, host.welcome)
host.send({"op": "post", "room": ROOM})
posted = host.wait("posted").get("room", {})
check("posting a room gives the host its id and an invite code", posted.get("id") and len(posted.get("invite", "")) == 6, posted)
rooms = sam.wait("rooms", cond=lambda m: m.get("rooms")).get("rooms", [])
check("everyone sees the open room (title, seats, host name), without its invite code",
      rooms and rooms[0]["title"] == "F-Zero GX" and rooms[0]["taken"] == 1 and rooms[0]["seats"] == 3
      and rooms[0]["host"]["name"] == "Host" and "invite" not in rooms[0], rooms)
host.send({"op": "post", "room": ROOM})
check("one room per host", "already" in host.wait("error_soft").get("msg", ""))
bad = dict(ROOM, seats=9)
sam.send({"op": "post", "room": bad})
check("a room has 2 to 4 seats", "2 to 4" in sam.wait("error_soft").get("msg", ""))

# ask to join, host accepts: both get stream keys for the same session
rid = posted["id"]
sam.send({"op": "join", "room": rid})
req = host.wait("request")
check("the host is asked: 'Sam wants to join'", req.get("player", {}).get("name") == "Sam", req)
check("Sam waits for the answer", sam.wait("waiting").get("room") == rid)
host.send({"op": "answer", "req": req.get("req"), "accept": True})
acc, st = sam.wait("accepted"), host.wait("seated")
check("accepted: Sam is Player 2 with a guest key; the host gets the host key",
      acc.get("seat") == 2 and st.get("seat") == 2 and acc.get("session", {}).get("key") and st.get("session", {}).get("key"), (acc, st))
s1 = sessions.lookup(acc.get("session", {}).get("key", ""))
s2 = sessions.lookup(st.get("session", {}).get("key", ""))
check("the two keys open the same stream session, as guest and host", s1 and s2 and s1[0] is s2[0] and s1[1] == "guest" and s2[1] == "host", (s1, s2))
check("the room shows its players to the people in it", [p["name"] for p in acc.get("room", {}).get("players", [])] == ["Host", "Sam"], acc)

# the host declines Bea, then turns on auto-accept and she gets straight in
bea.send({"op": "join", "invite": posted["invite"]})
req = host.wait("request")
host.send({"op": "answer", "req": req.get("req"), "accept": False})
check("a declined player is told kindly", "said no" in bea.wait("declined").get("why", ""))
host.send({"op": "update", "room": {"auto": True, "note": "Open seats, jump in"}})
time.sleep(0.2)
bea.send({"op": "join", "room": rid})
acc_b = bea.wait("accepted")
check("auto-accept: straight into the next seat (Player 3), own session", acc_b.get("seat") == 3 and acc_b.get("session", {}).get("key")
      and acc_b["session"]["key"] != acc.get("session", {}).get("key"), acc_b)
cy = P("Cy")
cy.send({"op": "join", "room": rid})
check("a full room says so", "full" in cy.wait("declined").get("why", ""))

# chat: lobby and room, blocking, masking, the speed limit
dee = P("Dee", blocked=[sam.id])
sam.send({"op": "chat", "to": "lobby", "text": "gg everyone, that badword race was close"})
m = cy.wait("chat")
check("lobby chat reaches everyone, with the lobby's own words masked", m.get("text") == "gg everyone, that ******* race was close"
      and m.get("from", {}).get("name") == "Sam", m)
check("a player who blocked Sam doesn't get his messages", dee.none("chat"))
sam.send({"op": "chat", "to": rid, "text": "<script>hi</script> room only"})
m_host, m_bea = host.wait("chat", cond=lambda m: m.get("to") == rid), bea.wait("chat", cond=lambda m: m.get("to") == rid)
check("room chat reaches the room only, without markup", m_host.get("text") == "scripthi/script room only" and m_bea and cy.none("chat"), (m_host, m_bea))
cy.send({"op": "chat", "to": rid, "text": "let me in"})
check("chat to a room you're not in is refused", "not in that room" in cy.wait("error_soft").get("msg", ""))
for i in range(7):
    cy.send({"op": "chat", "to": "lobby", "text": f"spam {i}"})
check("the chat limit (5 in 10 s) slows a spammer down", "Slow down" in cy.wait("error_soft").get("msg", ""))
late = P("Late")
check("someone who just arrived sees the recent lobby chat", any("gg everyone" in h["text"] for h in late.welcome.get("chat", [])), late.welcome.get("chat"))

# reports and moderation
admin = P("Owner", admin=True)
check("the lobby's own app (private token) is its moderator", admin.welcome.get("admin") is True)
bea.send({"op": "report", "player": cy.id, "why": "spam", "text": "flooding the chat"})
check("a report is confirmed", bea.wait("reported").get("player") == cy.id)
r = admin.wait("report")
check("the moderator gets the report, with the reported player's last messages", r.get("why") == "spam" and "spam 0" in str(r.get("their_last_messages")), r)
saved = open(os.path.join(data, "lobby-reports.jsonl")).read()
check("reports are kept in the lobby's private reports file", "flooding the chat" in saved and oct(os.stat(os.path.join(data, "lobby-reports.jsonl")).st_mode)[-3:] == "600")
sam.send({"op": "mod", "action": "ban", "player": cy.id})
check("only the moderator can moderate", "Only the person running" in sam.wait("error_soft").get("msg", ""))
admin.send({"op": "mod", "action": "mute", "player": cy.id, "minutes": 5})
time.sleep(0.2)
cy.send({"op": "chat", "to": "lobby", "text": "still here"})
check("a muted player can't chat", cy.wait("error_soft", 4, cond=lambda m: "muted" in m.get("msg", "")) != {})
admin.send({"op": "mod", "action": "ban", "player": cy.id})
check("a banned player is told and disconnected", "banned" in cy.wait("bye").get("why", ""))
again = socket.create_connection(("127.0.0.1", PORT), 3)
again.sendall((json.dumps({"op": "lobby", "v": 2, "me": cy.secret, "name": "Cy2"}) + "\n").encode())
check("and can't come back (the ban is saved)", "banned" in again.makefile().readline() and cy.id in open(os.path.join(data, "lobby-bans.json")).read())

# the host removes and bans a seated player; a host leaving closes the room for everyone
host.send({"op": "kick", "player": bea.id, "ban": True})
check("removed by the host", "removed you" in bea.wait("removed").get("why", ""))
check("the host hears the seat is free", host.wait("left").get("seat") == 3)
bea.send({"op": "join", "room": rid})
check("a player the host banned can't ask again", "isn't taking requests" in bea.wait("declined").get("why", ""))
host.close()
check("the host left: the room closes for the players in it", "left" in sam.wait("room_closed").get("why", ""))
check("and the session keys stop working", sessions.lookup(acc.get("session", {}).get("key", "")) is None)
check("and it's gone from the list", sam.wait("rooms", cond=lambda m: not m.get("rooms")) != {})

# a netplay room: the seated player gets the emulator's details (address only for direct connections)
nat_host = P("Netplay host")
nat_host.send({"op": "post", "room": dict(ROOM, mode="netplay", auto=True, native={"emu": "dolphin", "code": "ABCD1234", "ip": "6.6.6.6"})})
nr = nat_host.wait("posted").get("room", {})
sam.send({"op": "join", "room": nr.get("id")})
na = sam.wait("accepted")
check("netplay room: Dolphin host code, no session, no address the host typed in",
      na.get("native") == {"emu": "dolphin", "code": "ABCD1234"} and "session" not in na, na)
nat_host.send({"op": "update", "room": {"native": {"emu": "dolphin", "code": "WXYZ9876"}}})
check("a new Dolphin host code reaches the players already in the room", sam.wait("native").get("native") == {"emu": "dolphin", "code": "WXYZ9876"})
sam.send({"op": "leave"})
check("leaving frees the seat", nat_host.wait("left").get("seat") == 2)
nat_host.send({"op": "update", "room": {"native": {"emu": "retroarch", "port": 55435}}})
time.sleep(0.2)
sam.send({"op": "join", "room": nr.get("id")})
na = sam.wait("accepted")
check("direct netplay (RetroArch): the host's address and port", na.get("native", {}).get("port") == 55435 and na["native"].get("ip"), na)

old = P("Old")
old.send({"op": "lobby", "v": 1})
s = socket.create_connection(("127.0.0.1", PORT), 3)
s.sendall((json.dumps({"op": "lobby", "v": 1, "me": secrets.token_hex(16)}) + "\n").encode())
check("an older app is told to update", "newer Retro-Netplay" in s.makefile().readline())

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
