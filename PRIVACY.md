# Privacy

Retro-Netplay has no accounts, no analytics, no ads and no tracking. This page says exactly what leaves your device, and where.

## What the matchmaker sees
When you press "Find an opponent", your app sends the matchmaker: the game name, which lanes you offer, a label like `android` or `desktop-linux`, whether you can host, and any invite code. The matchmaker also sees your IP address (every server does). It hands your opponent your IP address and label, because the two of you must connect directly. It keeps this in memory only while you wait or play, and writes nothing to disk. Whoever runs a matchmaker can see this, so use one you trust. You can run your own: `python3 desktop/server.py`.

## What your opponent sees
Your IP address and app label (needed for the connection), plus your game name. In a built-in stream, the host's guest also sends controller button presses. The guest never gets the host's mouse, keyboard, files or desktop: only the chosen game window or screen is shared, and the guest's input becomes a virtual game controller.

## Streaming
- Video and sound travel directly between the two players, encrypted by WebRTC. The matchmaker only relays the setup messages (they contain network addresses, not video).
- The guest page is served by the matchmaker and loads nothing from any other site.
- To connect through home routers, WebRTC asks a STUN server for your public address. By default this is Google's public STUN server (`stun.l.google.com`), which therefore sees your IP address. To avoid third parties, run the matchmaker with the environment variable `NETPLAY_ICE_JSON='[]'` (works on a local network only) or point it at your own STUN/TURN server (see PROTOCOL.md).
- A TURN relay, if you add one, can see how much data passes through but not its content.

## Saved on your device
Settings, game list, controller layout and optional social settings, in `~/.netplay-matchmaker/` (Linux/Mac) or the app's folder (Windows), readable only by you (permissions 0600). Stream timing statistics go to `stream-stats.csv` in the same folder: numbers only (delays, frame rates), no video or names. Delete the folder to remove everything.

## Optional: posting your invite
Off by default. If you turn it on, the app sends your invite text and link to the Discord webhook or Telegram chat that you entered, and nothing else. Tokens stay on your device. Android uses the system share sheet instead and stores no logins.

## Your rights
The software keeps your data only on your device, so you control it: delete the settings folder to erase it. A matchmaker operator may see your IP address while you search; ask that operator about access or deletion. If you are in the EU/UK or California, a public matchmaker operator must tell you what it keeps and honour your rights.

## Test mode
`--demo` (for testing only) lets anyone who can reach the matchmaker create test streams. Never use it on a public matchmaker.

## Children
This software is for people aged 13 and over and collects no personal information. Parents of younger children should not let them play with strangers. Playing with strangers means exchanging IP addresses; use invite links with people you know.

This describes the software as shipped. If you run a public matchmaker for other people, you are responsible for telling them what your server logs and for following the privacy laws that apply to you.
