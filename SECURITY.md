# Security policy

Please report vulnerabilities privately using GitHub's **Security → Report a vulnerability** on this repository, and don't post details publicly or include anyone's personal data. Only the latest version receives fixes.

Notes for people who run a matchmaker: give it a domain name and a TLS certificate; keep stream keys secret (they work only for their own role and expire after two hours); and restrict any TURN relay to authenticated sessions.

## Built-in protections

- The matchmaker hangs up on a search that takes more than 10 seconds to arrive or is larger than 8 KB, limits each address to 8 waiting searches and 30 new searches a minute (so invite codes can't be guessed by brute force), and ignores anything sent after the search line.
- It forwards only short, flat values from one player to the other, and shares IP addresses only on the native (emulator) lane, where a direct connection needs them.
- To encrypt the apps' connections (and serve the invite page over https), start it with `NETPLAY_TLS_CERT=/path/fullchain.pem NETPLAY_TLS_KEY=/path/privkey.pem python3 desktop/server.py`. The apps then switch to TLS by themselves (see PROTOCOL.md, Encryption).
- The apps check everything they receive from a matchmaker (stream links, Parsec IDs, IP addresses) before opening or launching anything.
- The desktop app stores Discord and Telegram secrets with DPAPI (Windows), the Keychain (macOS) or the desktop keyring (Linux, via `secret-tool`), and writes its settings file readable by your account only. The Android app opts out of cloud backup and device transfer.
- Friends: your public friend code is a hash of a secret that never leaves your device except to the matchmaker, so a code can't be used to act as you. The matchmaker treats two players as friends only when both list each other, never shows your code to strangers, limits calls to 10 a minute, expires them after 60 seconds, and keeps the online list in memory only. Privileges are enforced by your own app (it decides whether to accept a call on its own).
- RetroArch network commands (UDP 55355) answer anyone on your local network while switched on. Don't forward that port on your router. The desktop app turns them on only for the RetroArch it starts.
- The Android controller shortcut is an accessibility service that filters key events only: it can't read windows (`canRetrieveWindowContent=false`), looks at Start and Select only, and never consumes a key press.
