# Security policy

Please report vulnerabilities privately using GitHub's **Security → Report a vulnerability** on this repository, and don't post details publicly or include anyone's personal data. Only the latest version receives fixes.

Notes for people who run a matchmaker: don't use `--demo` on a public server; put it behind HTTPS; keep stream keys secret (they work only for their own role and expire after two hours); and restrict any TURN relay to authenticated sessions.

## Built-in protections

- The matchmaker hangs up on a search that takes more than 10 seconds to arrive or is larger than 8 KB, limits each address to 8 waiting searches and 30 new searches a minute (so invite codes can't be guessed by brute force), and ignores anything sent after the search line.
- It forwards only short, flat values from one player to the other, and shares IP addresses only on the native (emulator) lane, where a direct connection needs them.
- To serve the stream pages and signaling over https, start it with `NETPLAY_TLS_CERT=/path/fullchain.pem NETPLAY_TLS_KEY=/path/privkey.pem python3 desktop/server.py`. Clients then open `https://` stream links automatically.
- The apps check everything they receive from a matchmaker (stream links, Parsec IDs, IP addresses) before opening or launching anything.
- The desktop app stores Discord and Telegram secrets with DPAPI (Windows), the Keychain (macOS) or the desktop keyring (Linux, via `secret-tool`), and writes its settings file readable by your account only. The Android app opts out of cloud backup and device transfer.
