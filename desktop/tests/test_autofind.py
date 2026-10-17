"""Nobody types an address at home: every device asks the network, and picks the same matchmaker by one rule
(running longest, then lowest address). Checks the answers, the rule, the TCP fallback and self-detection."""
import json, os, socket, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "desktop"))
import lan  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({info})" if info and not ok else ""))
    fails += 0 if ok else 1


def free_port():
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if p < 60000:
            return p


def start(port):
    data = tempfile.mkdtemp()
    pr = subprocess.Popen([sys.executable, "-u", os.path.join(ROOT, "desktop/server.py"), str(port)],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, "NETPLAY_DATA": data})
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return pr
        except OSError:
            time.sleep(0.05)
    raise SystemExit("server didn't start")


def ask_udp(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(lan.DISCOVER_ASK, ("127.0.0.1", port + 2))
    m = json.loads(s.recvfrom(2048)[0])
    s.close()
    return m


pa, pb = free_port(), free_port()
a = start(pa)
time.sleep(1.2)          # a has been running longer than b
b = start(pb)
try:
    ua, ub = ask_udp(pa), ask_udp(pb)
    check("discovery answer says how long it has run and which matchmaker it is",
          isinstance(ua.get("since"), int) and len(ua.get("id", "")) == 12, ua)
    check("two matchmakers have different ids", ua["id"] != ub["id"])
    check("the older one says it started earlier", ua["since"] <= ub["since"], (ua["since"], ub["since"]))

    ea, eb = lan.probe("127.0.0.1", pa), lan.probe("127.0.0.1", pb)
    check("TCP check (for routers that drop broadcasts) recognises a matchmaker", ea and ea["id"] == ua["id"], ea)
    check("TCP check ignores a port with nothing on it", lan.probe("127.0.0.1", free_port()) is None)

    # phones and computers see the answers in different orders and from different addresses: same pick
    ea["host"], eb["host"] = "10.0.0.183", "10.0.0.22"
    check("everyone picks the matchmaker that has run longest", lan.pick([eb, ea])["id"] == ua["id"])
    check("order of answers doesn't matter", lan.pick([ea, eb])["id"] == lan.pick([eb, ea])["id"])
    same = [{"host": "10.0.0.5", "since": 10, "id": ""}, {"host": "10.0.0.40", "since": 10, "id": ""}]
    check("a tie goes to the lowest address (numerically)", lan.pick(same)["host"] == "10.0.0.5")
    old = {"host": "10.0.0.9", "port": 7777, "name": "", "since": None, "id": ""}
    check("an older app that doesn't say how long it ran loses to one that does", lan.pick([old, eb])["host"] == "10.0.0.22")

    # the same matchmaker answering from two addresses (cable and Wi-Fi) counts once
    found = {}
    lan._merge(found, dict(ea, host="127.0.0.1"))
    lan._merge(found, dict(ea, host="10.0.0.22"))
    check("one matchmaker reached two ways is listed once, by its home-network address",
          len(found) == 1 and list(found.values())[0]["host"] == "10.0.0.22", found)

    # this computer recognises its own matchmaker by id (not by address, which can differ)
    import app
    app._local_mm.update(ok=True, id=ua["id"])
    cfg = {"server_port": pa}
    check("its own matchmaker is used as this computer (127.0.0.1)",
          app.choose_matchmaker(cfg, [eb, dict(ea, host="10.0.0.22")])[0] == "127.0.0.1")
    app._local_mm.update(ok=True, id=ub["id"])
    got = app.choose_matchmaker(cfg, [eb, ea])
    check("a computer whose matchmaker started later uses the older one on the network",
          got[0] == "10.0.0.183", got)
    check("choosing by itself stays on for home addresses",
          app.auto_matchmaker_on({"server_host": "10.0.0.22"}) and app.auto_matchmaker_on({"server_host": "127.0.0.1"}))
    check("an internet address typed on purpose is left alone", not app.auto_matchmaker_on({"server_host": "203.0.113.5"}))
    check("...and so is a home address when switched off", not app.auto_matchmaker_on({"server_host": "10.0.0.2", "auto_mm": False}))
finally:
    a.kill()
    b.kill()

print("all passed" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
