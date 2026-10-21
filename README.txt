NETPLAY MATCHMAKER  -  two apps, one matchmaker

  desktop/   Linux, Windows and Mac app (Python). Start with desktop/README.txt.
  android/   Android app (Java, open it in Android Studio).
  PROTOCOL.md  The few rules both apps follow so they can match each other.

HOW YOU PLAY (new)
  - Tap a game (phone) or press Play (computer). The game starts and you're in the queue at
    the same time. When someone is found, the player already in the game starts hosting inside
    it (no restart) and the other player joins.
  - Already in a game? Hold START + SELECT for 3 seconds: you're in the queue, and RetroArch
    shows "in the queue" on screen. Hold them again to leave.
    Phone: switch on "Retro-Netplay controller shortcut" once (setup shows you where), and turn
    on Network Commands in RetroArch (Settings > Network). Computer: it just works with RetroArch.
  - RetroArch on Android can't be told to join someone else's game by another app, so when the
    PHONE is the joining player, RetroArch shows exactly what to pick (Netplay > Refresh Netplay
    Host List > the host's name, or Connect to Netplay Host, address already copied).
  - FRIENDS: kept simple. Share your 6-character friend code (like K7M-4QZ); your friend types it
    under Add a friend and you accept. Then you see each other online and what you're playing.
    Tap Join next to a friend: they get Allow / Deny, and once they allow it they host and you
    connect straight to their game. Both apps must use the same matchmaker. If the matchmaker runs
    on the host's PC, a phone on the same Wi-Fi connects with Friends > Find on my network (or the
    PC's address shown in the PC's Friends window). On Linux, open the firewall once:
    sudo ufw allow 7777:7778/tcp && sudo ufw allow 7779/udp

HOW THEY WORK TOGETHER
  Both apps connect to the same matchmaker (desktop/server.py). Pick the same game with the
  exact same name and system on both and you're paired. Games found through Game folders are
  named the same way on every device, so this usually just works.
  - The matchmaker runs inside the desktop app (on by default), or on any always-on machine
    with:  python3 desktop/server.py
    At home, every device finds it by itself on the Wi-Fi: nothing to type. Over the internet,
    the matchmaker's computer needs port 7777 (TCP) reachable.
  - A phone can't host a stream and usually can't host netplay over mobile data, so the
    matchmaker always makes the COMPUTER the host when a phone is playing a computer.
  - Android <-> computer netplay is RetroArch to RetroArch. Both need the SAME core and the SAME
    game file. Android <-> computer streaming: the computer hosts, the phone joins.
    That is how a phone can play PS2 / PS3 games running on your PC.

BUILT-IN STREAMING (new, the default for PS2 / PS3)
  Our own low-latency streamer, so Linux can host (Parsec can't). The host's computer captures
  the game window and its sound, encodes it (on the graphics card if possible) and sends it over
  WebRTC. Joining feels like Parsec or emulator netplay: Friends > Join, your friend taps Allow,
  and the game appears in Retro-Netplay's own player window with your controller working. No web
  browser, nothing to copy or click. The guest's controller (or keyboard, or on-screen buttons on a
  phone) goes back and appears on the host as a virtual Xbox 360 controller = Player 2. A guest can
  only press game buttons; they never get your mouse, keyboard or desktop.
  - PLAYER WINDOW: F11 or Alt+Enter = full screen, F2 = the numbers (round trip, fps, decode time,
    lost packets), Esc twice = leave. Keyboard: Z/X/A/S = A/B/X/Y, Q/W = shoulders, arrows = d-pad,
    Enter = Start, Shift = Select. If the host lets you choose, press 2, 3 or 4 to be that player.
    If anything goes wrong it says why on screen, including the host computer's own error message.
  - The Linux download carries the player with its own GStreamer. It decodes the video on your
    graphics card (Intel/AMD with VA-API, or NVIDIA). A computer without one uses its own GStreamer
    instead, if it has a software decoder: Settings > Streaming check says what to install (usually
    one package, gstreamer1.0-libav). (The download can't include a software H.264 decoder: patents.)
  - The Android app still plays streams in its built-in web page; its own native player comes later.
  - HOST SETUP (Linux): run  python3 desktop/stream_host.py --doctor  . It lists exactly what is
    missing (GStreamer pieces, a hardware encoder, permission for the virtual controller) with the
    install commands. Hosting is Linux only for now. Joining: the Linux download has the player window
    built in; the Windows and Mac downloads with the player are not done yet (Settings > Streaming
    check says so on those computers).
  - On Wayland your desktop shows its own "share a window" picker: choose the game window.
  - Every session writes what the guest experienced (round trip, jitter buffer, decode time,
    frames per second, lost packets) AND what the host spent (capture age, convert, encode, pack)
    to ~/.netplay-matchmaker/stream-stats.csv (the joining computer writes the same file). Turn host timing
    off with --no-timing. The guest sees the same numbers on screen: F2 in the player window (on a phone:
    tap the little status pill, top right).
    Those numbers are how we tune latency.
  - Players only pair if both use the same streaming method (Built-in or Parsec, Settings).
  - Behind a strict router the stream may not connect directly. The fix is a TURN relay next to the
    matchmaker (below). The apps use it by themselves whenever the matchmaker has one; without one, the
    player window says so in plain words when the direct connection fails.

TURN RELAY (for friends behind strict routers; optional, for whoever runs the matchmaker)
  The relay passes the stream along when two devices can't reach each other directly. The video stays
  encrypted end to end: the relay only sees how much data passes. Nothing is set up by default, and no
  relay password is ever built into the apps: the matchmaker hands each player a password that works for
  3 hours, made from a secret it shares with the relay.
  1. On the matchmaker's computer (or any always-on server): sudo apt install coturn
  2. Make a long random secret:  openssl rand -hex 32
  3. In /etc/turnserver.conf put (your address and secret):
       listening-port=3478
       fingerprint
       use-auth-secret
       static-auth-secret=<the secret from step 2>
       realm=netplay
       no-cli
       no-multicast-peers
       denied-peer-ip=10.0.0.0-10.255.255.255
       denied-peer-ip=172.16.0.0-172.31.255.255
       denied-peer-ip=192.168.0.0-192.168.255.255
     (the denied-peer lines stop strangers from using your relay to reach your home network)
     then: sudo systemctl enable --now coturn
  4. Open UDP and TCP port 3478, and UDP ports 49152-65535, on that computer's router/firewall.
  5. Tell the matchmaker, before starting it:
       NETPLAY_TURN_URLS="turn:<your public address>:3478?transport=udp,turn:<your public address>:3478?transport=tcp"
       NETPLAY_TURN_SECRET="<the same secret>"
       python3 desktop/server.py
     Or, for the matchmaker inside the desktop app, put the same two values in
     ~/.netplay-matchmaker/server/turn.json as {"urls": ["turn:..."], "secret": "..."} and restart the app.
     The matchmaker prints "relay for strict routers: ..." when it found them.
  A player can switch the relay off in Settings ("Use the matchmaker's relay server...").


FIRST-TIME SETUP (both apps)
  The first time you open the app, a short setup walks you through everything once:
  1. Your games: pick the folders where you keep games. Every game inside is found and
     named automatically (so it matches the other player's name), and new games show up
     next time you open the app. If a file could be for two consoles, you're asked once
     per folder.
  2. Emulators (PC) / RetroArch (phone): found automatically where possible.
  3. Online: the matchmaker address, and "Open ports automatically". The app asks your
     router (UPnP / NAT-PMP) to open only the netplay port, then asks the matchmaker to
     test it, and tells you in plain words whether hosting will work.
  4. Controllers (PC).
  Run it again any time: Settings > Run setup again.

LEGAL AND PRIVACY
  PRIVACY.md says exactly what leaves your device. NOTICE.md lists third-party licenses, the
  Retro-Netplay is licensed under GPL-3.0 (see LICENSE).

TESTING WITHOUT A SECOND PERSON OR YOUR HOME PC
  Read PHONE-TEST-GUIDE.txt. `sh phone-test.sh` starts the matchmaker in test mode (python3 desktop/server.py --demo)
  and a pretend opponent (desktop/fake_opponent.py). Open http://127.0.0.1:7778/demo to try the stream page.
  Never use --demo on a public matchmaker: it lets anyone create test sessions.

INVITE LINKS
  Starting a search makes an invite link on either app. Friends who open it join you in a private
  match; everyone else can still match you at random (switchable). On a computer the invite is a
  netplay:// link that opens Retro-Netplay directly, plus a short code (like ABCD-2345) your friend can
  type under Enter code... instead (same matchmaker). Clicking a link while the app is open hands it to
  the open window. The desktop app can also post
  the link to Discord or Telegram for you if you connect them (off until you turn it on). The
  Android app uses the share sheet, so every social app you have works and no login is stored.
  The matchmaker serves a small web page for the Android app's links on port 7778 (TCP port + 1), so
  allow that port as well as 7777 if you host it.

ANDROID APP: BUILD AND INSTALL
  NO COMPUTER? Let GitHub build it. Put this whole folder in a GitHub repository (the file
  .github/workflows/android.yml is already here). Open the repo's Actions tab > "Build Android APK",
  and when it finishes download the artifact "NetplayMatchmaker-APK" on your phone, unzip it, and
  install app-debug.apk (Android will ask you to allow installing from your browser/files app).
  OR, with a computer:
  1. Install Android Studio and open the "android" folder. Accept any offer to update Gradle or
     the Android plugin. Press Run with your phone plugged in (USB debugging on),
     or Build > Build APK(s) and copy it to your phone.
  2. On the phone install RetroArch (Google Play) and, inside it, download the cores:
     Load Core > Download a Core > mGBA, PCSX ReARMed, Flycast. Streaming needs nothing extra: the app plays it.
  3. Open the app > Settings: type the matchmaker address (your PC's address) and port 7777.
  4. Add a game: its name (same as your opponent's), its system, and the full path to the game
     file, for example /storage/emulated/0/ROMs/game.gba. Leave the path blank for PS2 / PS3 games
     you'll only stream.
  5. Tap a game. RetroArch opens with it and you're in the queue. For PS2/PS3/GameCube games the
     phone joins a friend's stream instead: when matched, the stream plays inside the app (your controller works in it; swipe and press Back to leave).

WHAT WAS AND WASN'T TESTED
  Tested: the matchmaker rules, the desktop app's engine, and the Android app's matching logic
  (it was run against the real server with the desktop app as the other player).
  Tested for the built-in streamer (tests in desktop/tests/, run by the automatic build on every change):
  the native player window (with a pretend screen) against the REAL stream host over the matchmaker's
  TCP connection: video arrives at 60 fps, controller packets from a pretend SDL controller arrive intact,
  slot choice, wrong keys are refused, the host's real error is shown, old and new apps never pair, and
  the stream also works through a real TURN relay (coturn) with the matchmaker's short-lived password.
  The same test runs against the PACKAGED player from the Linux download (both ways it can start).
  Also: the Android app's web page in a real Chromium against the real host, access control, the
  host's session logic and the virtual-controller event translation, and the app's Join > Allow flow
  opening the player window by itself.
  Tested for queue and friends: friends-first matching, priority, friends-only, mutual adds, the
  online list and calls (server, Android core and desktop app against the real matchmaker), the
  desktop Play/hotkey flow with a pretend RetroArch, and the RetroArch commands against a
  pretend RetroArch.
  NOT tested: anything on real hardware. The player window on a real screen with a real controller,
  decoding on a real graphics card (VA-API / NVIDIA), sound, full screen on a real desktop; netplay://
  links opening the app on Windows and Mac; the controller shortcut and notifications on a real phone,
  a real RetroArch answering network commands, the real host's screen capture, GPU encoding and the
  virtual controller on a real /dev/uinput, the Wayland picker. So actual latency is unknown until you
  run it. Also not tested: the Android screens and the RetroArch launch (never built or run on a phone),
  and any real emulator session.
