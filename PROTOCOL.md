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
