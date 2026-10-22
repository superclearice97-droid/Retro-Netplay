"""A pretend opponent for testing the apps with no second person (works in Termux on a phone too).

  python3 server.py                   # terminal 1: the matchmaker
  python3 fake_opponent.py            # terminal 2: waits for YOUR app to search, then matches it

It plays the HOST: you (the app under test) become the guest. It checks matching only: on the built-in
stream your app's player opens, but no picture comes (that needs a real computer hosting the stream).
"""
import argparse
import json
import socket
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="127.0.0.1:7777", help="matchmaker host:port")
    ap.add_argument("--game", default="ps2:urban-reign", help="must equal the key your app uses: <system>:<name-with-dashes>")
    ap.add_argument("--lane", choices=["stream", "native"], default="stream")
    ap.add_argument("--backend", choices=["own", "parsec"], default="own", help="streaming method to pretend to use")
    ap.add_argument("--invite", help="also wait in this private invite room")
    ap.add_argument("--loop", action="store_true", help="after a match, wait for the next one")
    ap.add_argument("--client", default="desktop-linux", help="label shown to the other app")
    a = ap.parse_args()
    host, _, port = a.server.rpartition(":")
    host, port = host or "127.0.0.1", int(port)
    while True:
        req = {"v": 1, "op": "search", "game": a.game, "lanes": [a.lane], "client": a.client,
               "can_host": {a.lane: True}, "info": {a.lane: {"backend": a.backend, "sig": "tcp", "peer_id": "FAKEPEERID"} if a.lane == "stream" else {}}}
        if a.invite:
            req["invite"] = a.invite
        s = socket.create_connection((host, port))
        s.sendall((json.dumps(req) + "\n").encode())
        print(f"waiting for your app to search for {a.game} on the {a.lane} lane...", flush=True)
        f = s.makefile("r", encoding="utf-8")
        for line in f:
            m = json.loads(line)
            if m.get("op") == "error":
                sys.exit("matchmaker said: " + m.get("msg", "?"))
            if m.get("op") == "matched":
                print(f"\nMATCHED. You are the {'guest' if m['role'] == 'host' else 'host'}; I'm the {m['role']}.", flush=True)
                print(f"  your app's label: {m['peer'].get('client')}   peer ip: {m['peer'].get('ip')}", flush=True)
                if m.get("session"):
                    print("  (your app's player opens; a pretend opponent sends no picture)\n", flush=True)
                break
        s.close()
        if not a.loop:
            return
        print("(waiting for the next search; Ctrl+C to quit)\n", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
