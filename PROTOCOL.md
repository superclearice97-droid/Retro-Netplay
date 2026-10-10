# Matchmaker protocol v1

The desktop app (Windows/Linux/Mac) and the Android app are separate programs. They work together because they both speak this protocol to the same matchmaker server (`desktop/server.py`).

Transport: TCP, UTF-8, one JSON object per line (`\n`). One connection = one search. Closing the connection cancels it.

## Client -> server (first and only message)
```json
{"v":1, "op":"search",
 "game":"gba:pokemon-emerald",
 "lanes":["native","stream"],
 "info":{"stream":{"peer_id":"abc123"}},
 "can_host":{"native":true,"stream":false},
 "client":"android",
 "invite":"ABCD2345", "invite_only":false}
```
- `game` = `<system>:<slug>`. `slug` = lowercase the name, replace every run of characters outside `a-z0-9` with `-`, trim `-` from both ends. Both apps MUST build the key this way or players never match.
- System ids: `gba`, `ps1`, `dreamcast`, `gamecube`, `ps2`, `ps3`.
- `lanes`: `native` = the emulator's own netplay (rollback), `stream` = streaming (the built-in streamer, or Parsec).
- `info.<lane>`: anything the opponent needs to join you on that lane (e.g. `peer_id`). On the stream lane, `info.stream.backend` is `"own"` (built-in streamer) or `"parsec"` (also send `peer_id`). Two players are only paired on the stream lane if their backends are equal; a client that sends neither matches anyone.
- `can_host.<lane>`: can this device be the host on that lane? Default `true` if omitted. A phone cannot host a Parsec stream, and usually cannot host netplay over mobile data. The server only pairs two players if at least one can host.
- `invite` (optional): 4-32 letters/digits. The search also waits in a PRIVATE room made of `game` + this code, so a friend who follows your invite link meets you. `invite_only`: wait ONLY in that private room (what a friend following a link sends); omit it, or send false, to also stay open to random players.
- `client`: free-text label such as `desktop-linux` or `android`; shown to the opponent.

## Server -> client
- `{"op":"matched","v":1,"lane":"native","role":"host"|"guest","via":"public"|"invite","peer":{...info, "ip":"1.2.3.4","client":"android"}}` then the server closes the connection. `ip` is only included on the native lane. The server passes on only `info.<lane>` entries with short keys (letters, digits, `_`) and short text, number or true/false values.
- When both players asked for `backend:"own"` on the stream lane, the message also has `"session":{"key":"..."}` (host) or `"session":{"key":"...","play":"/play?t=..."}` (guest). If the server's web port uses https (`NETPLAY_TLS_CERT` and `NETPLAY_TLS_KEY` set), both also get `"web":"https"`. See Built-in streaming.
- `{"op":"ping"}` every 20 s while waiting. Ignore it.
- `{"op":"error","msg":"..."}` then the server closes the connection.

`peer.ip` is the address the server saw for the opponent, so a guest uses it to connect to a host (the host must have the right port open).

## Invite links
Matchmaker TCP port N, invite web page on HTTP port N+1 (`python3 server.py [N] [http_port]`, `0` turns the page off).

- Web link (what people share): `http://<host>:<N+1>/join?g=<game>&c=<code>&p=<N>`
- App link (what the page's button opens): `netplay://join?host=<host>&port=<N>&g=<game>&c=<code>`
- `g` must look like `<system>:<slug>`; `c` is the invite code. Apps MUST ignore any link that doesn't match these shapes, and MUST NOT run anything from it except starting an `invite_only` search on that host.
- Without `p`, the TCP port is the link's port minus 1.
- The page validates and escapes everything from the URL before showing it.

## Announcements (desktop app only)
The desktop app can post the invite text to a Discord webhook or a Telegram chat the player connected. It is off until the player turns it on, only sends the message template with the link, and waits at least the configured cooldown between posts. Android shares through the system share sheet instead, so no logins are stored.

## Built-in streaming (stream lane, backend "own")
The matchmaker also relays the WebRTC setup between the two matched players. Everything is plain HTTP on the web port (N+1), so a guest only needs a browser.

- Each side gets its OWN secret `key`. A key only works for its own role (`host` / `guest`). Sessions expire after 2 hours.
- Guest: open `http://<host>:<N+1><play>` (the `play` path from the match). That page is served by the matchmaker (`desktop/web/guest.html`).
- `GET /cfg?t=<key>` -> `{"iceServers":[...], "role":"host"|"guest", "game":"...", "v":1}`. STUN by default; set the `NETPLAY_ICE_JSON` environment variable on the server to add a TURN relay.
- `GET /sig?t=<key>&wait=<seconds, max 25>` -> `{"msgs":[...], "closed":false}`: messages for this role (long-poll). `404` = session over.
- `POST /sig?t=<key>` with one JSON object `{"type":"..."}` (max 16 KB): queued for the OTHER role. Types: `hello` (guest is ready), `offer` / `answer` (`sdp`), `ice` (`candidate`, `sdpMLineIndex`), `bye`.
- The host creates the offer after `hello` and adds two data channels: `pad` (unordered, no retransmits) and `ctl` (reliable, JSON).
- `pad` packet, 16 bytes little-endian: `ver u8 (=1), seq u8, buttons u32, lx ly rx ry i16, lt rt u8`. Button bit n = W3C standard-gamepad button n (12-15 = d-pad up/down/left/right). Sticks: -32767..32767, up = negative.
- `ctl` messages: guest `{"t":"hi"}`, `{"t":"ping","ts":n}`, `{"t":"stats","s":{...}}`; host `{"t":"slot","n":2}`, `{"t":"pong","ts":n}`, `{"t":"host","s":{captureAgeMs, convertMs, encodeMs, encodeP95, payMs, totalMs, frames}}` (every 2 s: how long the host spent on each stage, in milliseconds).

## Port check (optional)
A client may send one line instead of a search: `{"v":1,"op":"probe","port":55435,"token":"<8-64 hex digits>"}`. The server connects back to the address that sent it, on that port (1024-65535 only), reads one line, and answers `{"op":"probe","v":1,"port":55435,"open":true|false,"ip":"<your public address>"}`. `open` is true only if the line it read equals the token, so another program on that port doesn't count. The client listens on the port with a short-lived socket that answers with the token. Counts toward the same per-address rate limit as searches.

## Friends (optional)
Everything here is optional; a client that sends none of it behaves exactly as before.

**Identity.** Each install makes a random secret `me` (32 lowercase hex digits) and keeps it on the device. Its public id is the first 24 hex digits of `SHA-256(me)` (hashing the ASCII text). Friends share ids, never secrets, so nobody can act as you. A display `name` (up to 24 characters) is chosen by the player.

**Friend links.** Web: `http://<host>:<N+1>/friend?i=<id>&n=<name>`. App: `netplay://friend?i=<id>&n=<name>&host=<host>&port=<N>`. Adding a friend is local to the device. Two players are friends only when **both** have added each other (mutual); one-sided adds count for nothing on the server.

**Search fields.**
```json
{"me":"<secret>", "name":"Sam", "friends":["<id>",...], "priority":["<id>",...],
 "friend_wait":15, "friends_only":false, "prefer_host":true}
```
- Public pool order: a mutual friend marked priority (by either side) first, then other mutual friends (longest waiting first), then strangers.
- A player with friends waits `friend_wait` seconds (default 15, max 60) before strangers are allowed. `friends_only` never allows strangers. Invite rooms ignore all of this.
- `prefer_host`: when both players can host, this one hosts (the app sets it when you are already in the game).
- `matched.peer` gains `name` (if sent). Only for a mutual friend does it also carry `"friend":true` and `"id"`. A stranger never learns your id.
- At most 200 ids per list. Privileges other than priority are never sent to the server.

**Online list and calls (`hello` connection).** A client that wants friends to see it online opens a second connection and sends `{"v":1,"op":"hello","me":"<secret>","name":"Sam","friends":[...]}`, then keeps it open. It must send something (for example `{"op":"ping"}`) at least every 60 s.

Server -> client:
- `{"op":"hello","v":1,"id":"<your id>"}` once.
- `{"op":"friends","v":1,"online":[{"id","name","state","game"}]}` now and whenever a mutual friend comes online, goes offline or changes status. Only mutual friends are listed.
- `{"op":"call","v":1,"from":"<id>","name":"...","game":"<key>","kind":"play"|"watch","call":"<call id>"}`: a friend wants to join or watch.
- `{"op":"start","v":1,"call":"...","code":"<invite code>","game":"...","kind":"...","as":"caller"|"callee","with":{"id","name"}}`: the call was accepted. Both sides now run a normal search with `invite=code, invite_only=true` (the callee also sends `prefer_host:true`).
- `{"op":"declined","call":"...","name":"..."}`, `{"op":"call_failed","call":"...","msg":"..."}`, `{"op":"call_cancelled","call":"...","from":"<id>"}`, `{"op":"ping"}`.

Client -> server:
- `{"op":"status","state":"idle"|"queue"|"playing","game":"<key or empty>"}`
- `{"op":"call","to":"<id>","game":"<key>","kind":"play"|"watch","call":"<6-32 letters/digits>"}`. Fails unless the friend is mutual and online. At most 10 calls a minute; calls expire after 60 s.
- `{"op":"answer","to":"<caller id>","call":"...","ok":true|false}`. The callee's app decides; it may accept on its own when the caller has the matching privilege (join anytime for `play`, watch for `watch`).
- `{"op":"cancel_call","to":"<id>","call":"..."}`

**Privileges** live only on the device of the person who grants them: *join anytime* (auto-accept `play` calls), *watch* (auto-accept `watch` calls), *priority* (sent as `priority` in searches), *pick controller slot* (a stream host lets that friend choose Player 2-4).

The server keeps the online list and pending calls in memory only, and forgets them when the connection closes.

**Stream slot choice.** When the host allows it, its first `ctl` message is `{"t":"slot","n":2,"pick":[2,3,4]}`. The guest may answer `{"t":"want_slot","n":3}`; the host replies with a new `{"t":"slot","n":3,...}`.
