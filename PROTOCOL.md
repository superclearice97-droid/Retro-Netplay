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
- When both players asked for `backend:"own"` on the stream lane, the message also has `"session":{"key":"...","sig":"tcp"}`: this side's own secret key, to use on the stream connection (see Built-in streaming). A side whose app doesn't send `info.stream.sig:"tcp"` (the current Android app) gets the deprecated web page way instead: `"session":{"key":"...","play":"/play?t=..."}` (guest) or `{"key":"..."}` (host), plus `"web":"https"` if the server's web port uses https (`NETPLAY_TLS_CERT` and `NETPLAY_TLS_KEY` set).
- `{"op":"ping"}` every 20 s while waiting. Ignore it.
- A connection may first switch to TLS (see Encryption).
- `{"op":"error","msg":"..."}` then the server closes the connection.

`peer.ip` is the address the server saw for the opponent, so a guest uses it to connect to a host (the host must have the right port open).

## Invite links
Matchmaker TCP port N, invite web page on HTTP port N+1 (`python3 server.py [N] [http_port]`, `0` turns the page off).

- App link (what the desktop app shares): `netplay://join?host=<host>&port=<N>&g=<game>&c=<code>`
- Web link (what the Android app shares; its page's button opens the app link): `http://<host>:<N+1>/join?g=<game>&c=<code>&p=<N>`
- Invite code: the desktop app also shows the code (8 characters from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, shown as `ABCD-2345`) so a friend on the same matchmaker can type it. `{"v":1,"op":"invite","code":"ABCD2345"}` as the only line -> `{"op":"invite","v":1,"found":true|false,"game":"<game key>"}` (a search is waiting in that private room right now), then the server closes. It counts toward the per-address search limit. The app then runs the same `invite_only` search a link would.
- `g` must look like `<system>:<slug>`; `c` is the invite code. Apps MUST ignore any link that doesn't match these shapes, and MUST NOT run anything from it except starting an `invite_only` search on that host.
- Without `p`, the TCP port is the link's port minus 1.
- The page validates and escapes everything from the URL before showing it.

## Announcements (desktop app only)
The desktop app can post the invite text to a Discord webhook or a Telegram chat the player connected. It is off until the player turns it on, only sends the message template with the link, and waits at least the configured cooldown between posts. Android shares through the system share sheet instead, so no logins are stored.

## Built-in streaming (stream lane, backend "own")
The matchmaker also relays the WebRTC setup between the two matched players, over the same TCP port as everything else. The joining player never opens a web browser: the desktop app opens its own player window.

**Capability.** A search on the stream lane sends `"info":{"stream":{"backend":"own","sig":"tcp"}}` when the app signals over TCP (every desktop app from this version on). Two such apps get a TCP session. When only ONE of the two has `sig:"tcp"`, that one must be the host (it can still serve an older guest's web page); if it can't host, the two are never paired (in a friend's room both get `{"op":"error","why":"no_common_lane"}` saying one app is too old). Apps without `sig` pair with each other exactly as before.

**Stream connection.** Each matched player opens ONE more TCP connection to the matchmaker and sends, as its first line, `{"v":1,"op":"stream","key":"<its session key>"}`.
- Answer: `{"op":"stream","v":1,"role":"host"|"guest","game":"...","iceServers":[...],"relay":true|false}`. `iceServers` is a WebRTC list: STUN, plus the relay with a short-lived username and password when the matchmaker has one (`relay:true`; see TURN relay below).
- Or: `{"op":"error","msg":"...","why":"bad_key"}` (wrong key, a key of the other kind, or the session is over) and the connection closes.
- Then client -> server: `{"op":"sig","msg":{"type":"..."}}` (relayed to the OTHER role) and `{"op":"ping"}` at least every 60 s. Server -> client: `{"op":"sig","msg":{...}}` (from the other role, in order; messages sent before you connected wait for you), `{"op":"ping"}` every 20 s, `{"op":"error_soft","msg":"..."}` (that one message was refused) and `{"op":"error","msg":"...","why":"expired"|"rate"}` (then it closes).
- `msg` types: `hello` (guest is ready), `offer` / `answer` (`sdp`), `ice` (`candidate`, `sdpMLineIndex`), `status` (`text`: what that side is doing, shown on the other screen while connecting) and `bye` (`why`: the reason in plain words, shown to the other player; empty when they simply left). After a `bye` is handed over, the session is over and its connections close.
- Security: each side has its OWN key and a key only works for its own role and session. Keys of a TCP session never work on the web port, and keys of a web session never work here. Sessions end after 2 hours. One message is at most 16 KB, at most 200 wait unread per side, at most 300 per minute per connection, and opening stream connections counts toward the per-address limit for searches. A new connection with the same key replaces the old one (reconnecting after a network hiccup).

**Web page way (DEPRECATED).** Only for the current Android app, whose player is a web page in the app. Desktop apps never use it. It will be removed once the Android native player ships.
- Guest: open `http://<host>:<N+1><play>` (the `play` path from the match). That page is served by the matchmaker (`desktop/web/guest.html`).
- `GET /cfg?t=<key>` -> `{"iceServers":[...], "role":"host"|"guest", "game":"...", "v":1}`.
- `GET /sig?t=<key>&wait=<seconds, max 25>` -> `{"msgs":[...], "closed":false}`: messages for this role (long-poll). `404` = session over (or not a web session key).
- `POST /sig?t=<key>` with one `msg` object as above (max 16 KB): queued for the OTHER role. A web guest and a TCP host share one session: the matchmaker relays between the two ways.

**Media and controller (both ways of signaling).**
- The host creates the offer after `hello` and adds two data channels: `pad` (unordered, no retransmits) and `ctl` (reliable, JSON).
- `pad` packet, 16 bytes little-endian: `ver u8 (=1), seq u8, buttons u32, lx ly rx ry i16, lt rt u8`. Button bit n = W3C standard-gamepad button n (12-15 = d-pad up/down/left/right). Sticks: -32767..32767, up = negative.
- The guest's video uses a small jitter buffer; the host sends H.264 (constrained baseline) and Opus audio.
- `ctl` messages: guest `{"t":"hi"}`, `{"t":"ping","ts":n}`, `{"t":"stats","s":{...}}`; host `{"t":"slot","n":2}`, `{"t":"pong","ts":n}`, `{"t":"host","s":{captureAgeMs, convertMs, encodeMs, encodeP95, payMs, totalMs, frames}}` (every 2 s: how long the host spent on each stage, in milliseconds).

**TURN relay.** Behind strict routers the two devices can't reach each other directly. If whoever runs the matchmaker also runs a TURN relay (for example coturn, see README.txt), the matchmaker shares one secret with it and hands each player a password that works for 3 hours: `username = "<unix time it expires>:netplay-<role>"`, `credential = base64(HMAC-SHA1(secret, username))` (coturn's `use-auth-secret` / "TURN REST API"). Set `NETPLAY_TURN_URLS` (comma-separated `turn:` / `turns:` addresses) and `NETPLAY_TURN_SECRET` (16+ characters) on the matchmaker, or put `{"urls":[...],"secret":"..."}` in `turn.json` in its data folder. No relay is set up by default and no relay password is ever built into the apps. `NETPLAY_ICE_JSON` (a fixed JSON list) still works and replaces the default STUN list.

## Encryption (TLS)
A matchmaker whose operator set `NETPLAY_TLS_CERT` and `NETPLAY_TLS_KEY` (a certificate for its domain name, e.g. from Let's Encrypt) also offers TLS on the TCP port. Any connection may send, as its very first line, `{"v":1,"op":"starttls"}`. The answer is `{"op":"starttls","v":1,"ok":true}`, then the TLS handshake starts right away; or `{"op":"starttls","v":1,"ok":false}` (no certificate) and the connection carries on in plain text. Either way the client then sends its real first line (`search`, `hello`, `stream`, `invite`...). Plain connections keep working for older apps.
- The desktop apps ask for TLS whenever the matchmaker is reached by an internet domain name, and check the certificate against that name. Addresses (like `192.168.1.20` or `203.0.113.5`) and home-network names are never asked: certificates are made for names.
- Once a matchmaker has been seen with TLS, the app refuses to talk to it in plain text again (`~/.netplay-matchmaker/tls-matchmakers.json`), so nobody in between can quietly switch it off.
- An older matchmaker answers `starttls` with an error and closes; the app then starts over in plain text.

## Port check (optional)
A client may send one line instead of a search: `{"v":1,"op":"probe","port":55435,"token":"<8-64 hex digits>"}`. The server connects back to the address that sent it, on that port (1024-65535 only), reads one line, and answers `{"op":"probe","v":1,"port":55435,"open":true|false,"ip":"<your public address>"}`. `open` is true only if the line it read equals the token, so another program on that port doesn't count. The client listens on the port with a short-lived socket that answers with the token. Counts toward the same per-address rate limit as searches.

## Friends (optional)
Everything here is optional; a client that sends none of it behaves exactly as before.

**Identity.** Each install makes a random secret `me` (32 lowercase hex digits) and keeps it on the device. Its public id is the first 24 hex digits of `SHA-256(me)` (hashing the ASCII text). Friends share ids, never secrets, so nobody can act as you. A display `name` (up to 24 characters) is chosen by the player.

**Friend codes.** The matchmaker gives each player a short code, 6 characters from `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`, shown as `K7M-4QZ` (the `hello` reply carries it). It is the same every time on that matchmaker; the server stores only code -> player id and the player's chosen name (`friend-codes.json` in `NETPLAY_DATA`, default `~/.netplay-matchmaker/server/`).

**Friend links.** Web: `http://<host>:<N+1>/friend?c=<code>&n=<name>`. App: `netplay://friend?c=<code>&n=<name>&host=<host>&port=<N>`. Older links with `i=<id>` still work. Two players are friends only when **both** have each other in their lists (mutual); one-sided adds count for nothing on the server.

**Requests (on the `hello` connection).** `{"op":"lookup","code":"K7M4QZ"}` -> `{"op":"lookup","code":"K7M-4QZ","found":true,"id":"...","name":"Sam","self":false}`. `{"op":"friend_request","to":"<id>"}` -> the other player gets `{"op":"friend_request","from":"<id>","name":"...","code":"..."}` (now, or when they next connect; kept in memory for 14 days), and you get `{"op":"request_sent"}`. Accepting: `{"op":"friend_accept","to":"<id>"}` -> they get `{"op":"friend_accept","from":...,"name":...,"code":...}`. Lookups, requests and accepts together are limited to 20 a minute (`{"op":"error_soft"}` after that). The client adds the friend to its own list when it sends a request (marked "request sent") or accepts one, and sends `{"op":"set_friends","friends":["<id>",...]}` on the same connection so the server sees the new list (no reconnect). `"hidden":true` in `hello` (older apps) keeps you out of friends' online lists while still receiving requests and calls.

**Search fields.**
```json
{"me":"<secret>", "name":"Sam", "prefer_host":true}
```
- `me` tells the server who you are, so in a friend's join it knows which player said yes (that player hosts).
- `prefer_host`: when both players can host, this one hosts (the app sets it when you are already in the game, or when you allowed a friend in).
- Current apps send only these. The server still understands the older friends-first fields (`friends`, `priority`, `friend_wait`, `friends_only`): mutual friends first (priority before others), strangers only after `friend_wait` seconds (default 15, max 60), never with `friends_only`. Invite rooms ignore all of this.
- `matched.peer` gains `name` (if sent). Only for a mutual friend does it also carry `"friend":true` and `"id"`. A stranger never learns your id.
- At most 200 ids per list. Privileges other than priority are never sent to the server.

**Online list and calls (`hello` connection).** A client that wants friends to see it online opens a second connection and sends `{"v":1,"op":"hello","me":"<secret>","name":"Sam","friends":[...]}`, then keeps it open. It must send something (for example `{"op":"ping"}`) at least every 60 s.

Server -> client:
- `{"op":"hello","v":1,"id":"<your id>"}` once.
- `{"op":"friends","v":1,"online":[{"id","name","state","game"}]}` now and whenever a mutual friend comes online, goes offline or changes status. Only mutual friends are listed.
- `{"op":"call","v":1,"from":"<id>","name":"...","game":"<key>","kind":"play","call":"<call id>"}`: a friend wants to join you (Join in their Friends list).
- `{"op":"start","v":1,"call":"...","code":"<invite code>","game":"...","kind":"play","as":"caller"|"callee","with":{"id","name"}}`: the join was allowed. Both sides now run a normal search with `invite=code, invite_only=true`. The callee (who allowed it) hosts on every lane it can host; the caller connects to it. The server enforces this for 10 minutes using `me` from the searches; the callee also sends `prefer_host:true` and the caller `false`.
- `{"op":"declined","call":"...","name":"...","msg":"..."}` (`msg` optional: why, e.g. the friend doesn't have the game), `{"op":"call_failed","call":"...","msg":"..."}`, `{"op":"call_cancelled","call":"...","from":"<id>"}`, `{"op":"ping"}`.

Client -> server:
- `{"op":"status","state":"idle"|"queue"|"playing","game":"<key or empty>"}`
- `{"op":"call","to":"<id>","game":"<key>","kind":"play","call":"<6-32 letters/digits>"}`. Fails unless the friend is mutual and online. At most 10 calls a minute; calls expire after 60 s. (`"watch"` from older apps is still passed on.)
- `{"op":"answer","to":"<caller id>","call":"...","ok":true|false,"msg":"..."}`. The person being joined always decides (Allow / Deny). Their app says no by itself, with `msg`, when it doesn't have the game or is already playing with someone.
- `{"op":"cancel_call","to":"<id>","call":"..."}`
- `{"op":"set_friends","friends":["<id>",...]}`: replace your friend list; online lists are re-sent to you and to everyone added or removed.

The server keeps the online list and pending calls in memory only, and forgets them when the connection closes.

**Stream slot choice.** When the host allows it, its first `ctl` message is `{"t":"slot","n":2,"pick":[2,3,4]}`. The guest may answer `{"t":"want_slot","n":3}`; the host replies with a new `{"t":"slot","n":3,...}`.

## Finding a matchmaker on a home network
A client broadcasts the ASCII text `RETRO-NETPLAY-DISCOVER/1` by UDP to port N+2 (7779 by default), to `255.255.255.255` and the network's own broadcast address. A matchmaker answers to the sender with `{"app":"retro-netplay","v":1,"port":N,"web":N+1,"name":"<computer name>","since":<start time, seconds since 1970>,"id":"<12 hex characters, new each start>"}`. If no answer arrives, clients try TCP port N on every address of their /24 home network with `whoami` (below). **Which one to use:** answers with the same `id` are one matchmaker (reached by two addresses); of the rest, every client uses the one with the smallest `since` (running longest; missing counts as newest), then the lowest IPv4 address. Because every app applies this rule, all devices on a network end up on the same matchmaker without anyone choosing. Apps look again when the connection drops (at most every 20 seconds, and every 30 seconds while down), unless the player set an internet (non-private) address on purpose. At most 20 answers per address per 10 seconds. Links made on the computer that runs the matchmaker use that computer's home-network address (or the public address set for links), never 127.0.0.1. Clients treat 127.0.0.1 / localhost in a link or setting as "not set".

## Joining a friend, and "whoami"
- Emulator netplay address: `matched.peer.ip` is the host's address as the matchmaker sees it. If the host runs on the matchmaker's own computer (127.0.0.1), the guest gets the address the guest used to reach the matchmaker instead (for a phone at home: the PC's Wi-Fi address). If the host is on the matchmaker's home network and the guest is not, `peer` also has `"at_matchmaker":true`, and the guest connects to the matchmaker's address (the router must forward the netplay port).
- `status` may carry `"can":{"native":bool,"stream":bool}`: what that player can host right now. It is passed on in friends' online lists so their app can tell whether joining will work before asking.
- A join is a `call` with `kind:"play"`; the host's app shows an approval card (or accepts on its own with the "join anytime" privilege).
- In a private room (invite or call), if the two players can never be paired (no shared lane, or nobody can host), the server answers both with `{"op":"error","msg":"...","why":"no_common_lane"}` instead of letting them wait.
- `{"v":1,"op":"whoami"}` as the only line: the server answers `{"op":"whoami","v":1,"ip":"<the address it sees>","app":"retro-netplay","since":...,"id":"...","name":"..."}` and closes. Apps use it to show your internet address.
