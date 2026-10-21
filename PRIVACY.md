# Privacy

Retro-Netplay has no accounts, no analytics, no ads and no tracking. This page says exactly what leaves your device, and where.

## What the matchmaker sees
When you join the queue (by starting a game, with the controller shortcut, or by playing with a friend), your app sends the matchmaker: the game name, which lanes you offer, a label like `android` or `desktop-linux`, whether you can host, and any invite code. The matchmaker also sees your IP address (every server does). It hands your opponent your label, and, only for emulator netplay (where the two of you connect directly), your IP address. Stream matches don't share your IP address through the matchmaker. It keeps this in memory only while you wait or play, and writes nothing to disk. Whoever runs a matchmaker can see this, so use one you trust. You can run your own: `python3 desktop/server.py`. When you use a matchmaker on the internet by its name and its operator set up a certificate, the desktop app encrypts everything it sends there (TLS), so others on the way can't read it; the Android app does the same. On your home network, and for older apps, these connections are not encrypted.

## Friends
Friends are optional. To make them work, each install creates a random secret code that stays on your device. Your **friend code** (shown in the app) is made from it, so nobody can pretend to be you. When you go online, the app sends the matchmaker your secret, your chosen name and the friend codes of the people you added; a search sends only your secret and name. The matchmaker uses this only to show friends to each other and to know who hosts when a friend joins you, and keeps it in memory only while you're connected.

- The matchmaker gives you a short **friend code** (like K7M-4QZ). To make codes work when you're offline, it saves your code, your hidden player id and the name you chose in a small file on the matchmaker; nothing else (no IP address, no friends list). Anyone who knows your code can send you a friend request and see your chosen name; nothing more until you accept.
- Friend requests wait on the matchmaker (in memory, up to 14 days) until you connect. They're deleted when delivered, or when the matchmaker restarts.
- Two people are friends only when **both** add each other (sending a request and accepting it does that). Someone who adds you on their own sees nothing about you.
- Your friend code is shown only to your mutual friends, never to a stranger you're matched with. Strangers see the name you chose, so don't use your real full name if you'd rather not.
- **Online list:** while the app is open, or while you're queued or playing through it, your mutual friends can see that you're online, your name, and which game you're queued for or playing. Turn this off in Settings ("Let friends see when I'm online"); requests and calls still reach you.
- **Privileges** you give a friend (join anytime, watch, pick a controller slot) are stored only on your device and never sent anywhere. "Priority" is sent to the matchmaker, because it does the matching.
- **Calls** ("play with me", "let me watch") pass through the matchmaker to that one friend and are forgotten after a minute.

## Controller shortcut (Android, optional)
Holding Start + Select for 3 seconds to join the queue needs an Android accessibility switch, because that is the only way an app can see buttons pressed while another app (RetroArch) is on screen. It is off until you switch it on in Android's Accessibility settings. It looks only at the Start and Select buttons; it never stores or sends any button press, cannot read your screen, and always passes every press on to the game. Switch it off there at any time.

## Talking to RetroArch
The app talks to RetroArch on the same device (its "Network Commands" feature, a UDP port on your device) to ask which game is running, to start hosting, and to show short messages on the game screen. Nothing goes over the internet for this. The desktop app turns this feature on for the RetroArch it starts; on Android you switch it on in RetroArch yourself. Other devices on your home network could also send commands to RetroArch while this is on (for example pause it); your router doesn't let the internet reach it.

## What your opponent sees
Your chosen name, your app label and game name, and for emulator netplay your IP address (needed for the direct connection). In a built-in stream, the host's guest also sends controller button presses. The guest never gets the host's mouse, keyboard, files or desktop: only the chosen game window or screen is shared, and the guest's input becomes a virtual game controller.

## Streaming
- Video and sound travel directly between the two players, encrypted by WebRTC. The matchmaker only relays the setup messages (they contain network addresses, not video).
- On a computer, the friend's game opens in the app's own player window; no web browser is used, and nothing is loaded from any other site. The setup messages go through your normal connection to the matchmaker. While the stream starts, the player tells the host's computer how its connection is going (for example "connection failed"), and the host tells the player what it is doing or why it stopped; each side keeps this in `stream-host.log` / `stream-guest.log`.
- On Android, the stream plays in the app's own player in the same way: the setup messages go through the app's connection to the matchmaker, and the video comes from your friend's computer (directly, or through the relay below). No web page is loaded. The player sends your friend's computer only your button presses and playback numbers (delays, frame rate); it never uses the microphone or camera, and the app doesn't ask for them. If your friend's computer runs an older version without this, its stream page from the matchmaker opens inside the app instead (a built-in web view that only shows that page).
- To connect through home routers, WebRTC asks a STUN server for your public address. By default this is Google's public STUN server (`stun.l.google.com`), which therefore sees your IP address. To avoid third parties, run the matchmaker with the environment variable `NETPLAY_ICE_JSON='[]'` (works on a local network only) or point it at your own STUN/TURN server (see PROTOCOL.md).
- **Relay server.** If whoever runs your matchmaker also runs a TURN relay, and your two devices can't reach each other directly (strict routers), the stream goes through that relay instead. The matchmaker then gives your app a password for it that expires after 3 hours. The relay sees both IP addresses and how much data passes, but not the video, sound or button presses (they stay encrypted end to end). This is on by default so friends behind strict routers can play; switch it off in Settings ("Use the matchmaker's relay server...", on a computer and on Android). No relay is set up unless the matchmaker's operator adds one, and the apps never contain relay passwords.

## Finding a matchmaker on your home network
The apps find the matchmaker on your home network by themselves, so nobody has to type an address. They send one small broadcast on your Wi-Fi or home network asking whether a Retro-Netplay matchmaker is there; a matchmaker answers with its port numbers, the computer's name, when it started and a random number that identifies that run of it (a new one each time it starts). If no answer comes back (some routers drop broadcasts), the app instead knocks on each address of your home network (for example 192.168.1.1 to 192.168.1.254) on port 7777 and asks the same question. This happens when the app opens, and again while it can't reach the matchmaker. Nothing leaves your home network for this, and nothing about you is sent in the question. The desktop app runs the matchmaker by default (you can turn this off in Settings with "Run the matchmaker on this PC"); while it runs, devices on the same network can reach it on ports 7777-7778 (TCP) and 7779 (UDP). If you type an internet address on purpose, the app uses it and stops looking.

## Your address
"Your address" shows the addresses friends can use to reach you: your home-network address (worked out on your device), and your internet address, which the app first asks your own router for (UPnP) or the matchmaker you use. Only if you press **Look it up online** does it ask a public service (api.ipify.org), which then sees your internet address like any website would. Copying an address and sending it to someone is your choice; anyone with it can try to connect to your matchmaker.

## Joining friends
While you play or wait in the queue, your mutual friends also see whether your device can host (emulator netplay and/or a stream), so their app can tell them whether joining will work. A friend's join request shows on your screen as a card; nothing happens unless you tap Allow.

## Game folders
When you pick game folders, the app reads the file names in them, and the first few kilobytes of .iso and .bin files to tell which console they are for. This happens on your device only. The list of games stays on your device; only the name of the game you search for is sent to the matchmaker.

## Automatic port opening
If "Open ports automatically" is on (it is by default, and you can turn it off in Settings), the app asks your own router, over your home network only, to open the one port that netplay hosting needs (RetroArch 55435 TCP; Dolphin 2626 UDP on a PC). It uses the standard UPnP or NAT-PMP requests, renews them while the app runs and removes them when the desktop app closes. On Android, the opening expires on its own after two hours. Android needs the "view Wi-Fi connections" permission only to learn the router's address for this.

To check that the port really works, the app asks the matchmaker to connect back to your address on that port once. The matchmaker only ever connects to the address that asked, only to ports 1024 and above, and only counts the test as passed if this app answers with a one-time code. It tells you the public address it saw; it does not keep it.

An open port lets other people's emulators connect to yours while you host. Turn the option off if you never host.

## Saved on your device
Settings, game list, friends, your secret code, controller layout and optional social settings, in `~/.netplay-matchmaker/` (Linux/Mac) or the app's folder (Windows), readable only by you (permissions 0600). Stream timing statistics go to `stream-stats.csv` in the same folder, on the host's and on the joining player's computer: numbers only (delays, frame rates), no video or names. When you join a stream, what the player window did (messages and errors, no video or names) goes to `stream-guest.log`, replaced each time. When you host a stream, what the stream did (messages and errors, no video or names) goes to `stream-host.log`, replaced each time, and your desktop's token for "share the same window as last time" goes to `share-choice.token`, so the "share your screen" box only asks once. Delete that file to be asked again. If that box doesn't appear, the app restarts your desktop's own screen-sharing service (your user's, no admin rights) and asks again, and Settings > Streaming check > Test screen sharing shows that service's messages from the last 10 minutes so you can see what went wrong. These are read on your computer and sent nowhere. While a stream starts, short progress messages such as "waiting for the game window to be chosen" are shown on your friend's screen. Delete the folder to remove everything. On Android, the app keeps its settings, friends and which matchmakers it has seen encrypted in its own private storage (not in backups); uninstalling removes them.

## Optional: posting your invite
Off by default. If you turn it on, the app sends your invite text and link to the Discord webhook or Telegram chat that you entered, and nothing else. Tokens stay on your device: on Windows they are encrypted with your Windows account (DPAPI), on macOS they go in your Keychain, and on Linux in your desktop keyring when `secret-tool` is installed (otherwise in a settings file only your user account can read). Android uses the system share sheet instead and stores no logins; its settings are excluded from cloud backups and phone transfers.

## Your rights
The software keeps your data only on your device, so you control it: delete the settings folder to erase it. A matchmaker operator may see your IP address while you search; ask that operator about access or deletion. If you are in the EU/UK or California, a public matchmaker operator must tell you what it keeps and honour your rights.

## Test mode
`--demo` (for testing only) lets anyone who can reach the matchmaker create test streams. Never use it on a public matchmaker.

## Children
This software is for people aged 13 and over and collects no personal information. Parents of younger children should not let them play with strangers. Playing with strangers means exchanging IP addresses; use friends or invite links with people you know, and turn on "Only match me with friends".

This describes the software as shipped. If you run a public matchmaker for other people, you are responsible for telling them what your server logs and for following the privacy laws that apply to you.
