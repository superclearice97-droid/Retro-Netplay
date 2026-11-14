RETRO-NETPLAY  -  your old games, online, arcade style

WHAT YOU NEED
  - A computer (Linux works best; Windows and Mac are early tests) or an Android phone.
  - The emulators for your games. Retro-Netplay starts them for you:
      Game Boy Advance, PS1, Dreamcast:  RetroArch (with the mGBA, PCSX ReARMed and Flycast cores)
      GameCube and Wii:                  Dolphin
      PlayStation 2:                     PCSX2
      PlayStation 3:                     RPCS3
  - Your games (files you own). Retro-Netplay never includes or sends anyone game files.

INSTALL
  Linux:   download Retro-Netplay-x86_64.AppImage, then double-click it. If it won't open:
           right-click > Properties > Permissions > Allow executing as program.
           (Or the zip: unzip, open the folder, double-click retro-netplay.)
  Windows: unzip Retro-Netplay-windows-x86_64.zip and double-click Retro-Netplay (early test).
  Mac:     unzip, move Retro-Netplay to Applications and open it. The first time, macOS refuses because the
           app isn't signed by Apple: System Settings > Privacy & Security > Open Anyway (early test).
  Android: install Retro-Netplay.apk.

FIRST START
  1. Pick a player name (others see it) and confirm you're 13 or older.
  2. My games > Add a folder: pick where your games are. Games inside (and in subfolders) are found and named
     by themselves. If Skraper, EmulationStation or RetroArch made box art and details next to them, click a
     game to see its page.
  3. You're in this computer's own lobby. To play with people outside your home, join a lobby someone shares
     (Settings > Lobby, or click their invite link), or share yours (below).

JOIN A ROOM
  Arcade shows the open rooms in the lobby, one tile per room, coloured by console:
    "Just join"        a stream room: you see the host's game and control your player. You need nothing else.
    "Bring your copy"  a netplay room: you need the same game in My games. Greyed out if you don't have it.
  Click a tile. The host gets your request and lets you in (some rooms let you straight in). Then:
    - stream room: the player window opens. F11 full screen, F2 numbers, Esc twice to leave.
    - RetroArch room: RetroArch opens already connected to the host.
    - Dolphin room: Dolphin opens with its NetPlay window filled in (the host's code too, once the host shares
      it). In Dolphin: Tools > NetPlay > Connect, then press Connect.
  Friends can also type the room's invite code in the box at the top of Arcade.

HOST A ROOM
  Host a game (or a game's page > Host this game): pick the game, the number of players (2 to 4), a note, and:
    - How players join (for netplay games): Netplay (smoothest, each player needs the game) or Stream (anyone
      can join with just the app).
    - Let people in without asking: on, and free seats fill by themselves; off, and you accept each request.
    - Show the room in the arcade: off, and only people with the invite code can find it.
  Open the room: your game starts right away, so you play while people come in.
    - Stream rooms: the first time, your desktop asks which window to share: pick the game window.
    - Dolphin rooms: in Dolphin, Tools > NetPlay > Host (already set up), pick the game, then copy Dolphin's host
      code into the box in your room so players can connect.
  In the room, click "..." next to a player to remove them, ban them from the room, block or report them.

SHARE YOUR LOBBY (or join someone's)
  Settings > Lobby shows your lobby's addresses:
    - People on your Wi-Fi: your home address. Phones on the same Wi-Fi find the lobby by themselves; other
      computers type this address under Settings > Lobby, or click the invite link.
    - Everyone else: your public address. The app asks your router to let them in (UPnP or NAT-PMP). If your
      router doesn't allow that, turn on UPnP in its settings, or forward TCP port 7777 to this computer.
  "Copy invite link" copies a netplay://lobby link: send it, and a click opens Retro-Netplay in your lobby.
  To join someone else's lobby, click their link, or type their address under Settings > Lobby.
  The lobby's name and rules: put {"name": "...", "rules": "..."} in ~/.netplay-matchmaker/server/lobby.json
  and restart the app.

KEEPING YOUR LOBBY SAFE (if you run one)
  You moderate the lobby on your computer: click a name in chat or a room to mute (10 minutes), remove or ban
  them. Reports arrive in your app and are kept in ~/.netplay-matchmaker/server/lobby-reports.jsonl; bans in
  lobby-bans.json (to unban someone, remove their player id from that file and restart the app). Extra words to hide in chat: one per line in
  lobby-words.txt. Everyone can block people and has a swear-word filter (Settings > Chat). See TERMS.md.

PHONE
  The phone app has the same Arcade. It finds a lobby on your Wi-Fi by itself, or type an address in
  Settings. Stream rooms open in the app's player (a controller or the on-screen buttons). RetroArch rooms open
  RetroArch, with the host's address copied: in RetroArch, Netplay > Connect to Netplay Host, and paste it.
  Hosting from a phone comes later.

IF SOMETHING GOES WRONG
  - "Not connected": the lobby's computer is off, or its router doesn't let you in. Ask for its address again.
  - Stream won't connect: behind some routers the two computers can't reach each other directly. Whoever runs
    the lobby can add a relay (below).
  - Logs: ~/.netplay-matchmaker/stream-host.log (hosting), stream-guest.log (joining), and the window's own
    log in ~/.config/Retro-Netplay/engine.log. They contain no video or names.
  - If the app or a check ever uses far too much memory, it's stopped and memory-report.txt says which part.

FOR WHOEVER RUNS A LOBBY ON A SERVER
  A lobby can also run on its own, without the app:  python3 desktop/server.py [port]   (default 7777; TCP)
  Its data folder is ~/.netplay-matchmaker/server/ (or NETPLAY_DATA). Moderating from the app needs the token
  in lobby-admin.token there, so moderate from the app on the same computer and account.

  ENCRYPTION: give the server a domain name and a certificate (for example from Let's Encrypt) and start it with
  NETPLAY_TLS_CERT=/path/fullchain.pem NETPLAY_TLS_KEY=/path/privkey.pem. Apps that use it by that name then
  encrypt everything they send it, and refuse to connect if the encryption ever disappears.

  RELAY (for players behind strict routers): the relay passes the stream along when two devices can't reach
  each other. The video stays encrypted end to end; no relay password is built into the apps: the lobby hands
  each player a password that works for 3 hours, made from a secret it shares with the relay.
  1. sudo apt install coturn
  2. Make a long random secret:  openssl rand -hex 32
  3. In /etc/turnserver.conf:
       listening-port=3478
       fingerprint
       use-auth-secret
       static-auth-secret=<the secret>
       realm=netplay
       no-cli
       no-multicast-peers
       denied-peer-ip=10.0.0.0-10.255.255.255
       denied-peer-ip=172.16.0.0-172.31.255.255
       denied-peer-ip=192.168.0.0-192.168.255.255
     (the denied-peer lines stop strangers from using your relay to reach your home network)
     then: sudo systemctl enable --now coturn
  4. Open UDP and TCP port 3478, and UDP ports 49152-65535, on that computer's router/firewall.
  5. Start the lobby with NETPLAY_TURN_URLS="turn:<public address>:3478?transport=udp,turn:<public address>:3478?transport=tcp"
     and NETPLAY_TURN_SECRET="<the secret>", or put {"urls": ["turn:..."], "secret": "..."} in turn.json in
     its data folder. It prints "relay for strict routers: ..." when it found them.

LEGAL AND PRIVACY
  No games, BIOS or firmware are included: use games you own. For people aged 13 and over. See TERMS.md,
  PRIVACY.md, NOTICE.md and LEGAL.md. Licence GPL-3.0.
