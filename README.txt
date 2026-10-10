NETPLAY MATCHMAKER  -  two apps, one matchmaker

  desktop/   Linux, Windows and Mac app (Python). Start with desktop/README.txt.
  android/   Android app (Java, open it in Android Studio).
  PROTOCOL.md  The few rules both apps follow so they can match each other.

HOW THEY WORK TOGETHER
  Both apps connect to the same matchmaker (desktop/server.py). Pick the same game with the
  exact same name and system on both, press the big button, and you're paired.
  - The matchmaker runs inside the desktop app ("Run the matchmaker on this PC" in Settings),
    or on any always-on machine with:  python3 desktop/server.py
    The host needs port 7777 (TCP) reachable from the internet.
  - A phone can't host a stream and usually can't host netplay over mobile data, so the
    matchmaker always makes the COMPUTER the host when a phone is playing a computer.
  - Android <-> computer netplay is RetroArch to RetroArch. Both need the SAME core and the SAME
    game file. Android <-> computer streaming: the computer hosts, the phone joins.
    That is how a phone can play PS2 / PS3 games running on your PC.

BUILT-IN STREAMING (new, the default for PS2 / PS3)
  Our own low-latency streamer, so Linux can host (Parsec can't). The host's computer captures
  the game window and its sound, encodes it (on the graphics card if possible) and sends it over
  WebRTC. The guest just opens a link in a web browser: nothing to install, and no pairing PIN.
  The guest's controller (or keyboard, or on-screen buttons on a phone) goes back and appears on
  the host as a virtual Xbox 360 controller = Player 2. A guest can only press game buttons;
  they never get your mouse, keyboard or desktop.
  - HOST SETUP (Linux): run  python3 desktop/stream_host.py --doctor  . It lists exactly what is
    missing (GStreamer pieces, a hardware encoder, permission for the virtual controller) with the
    install commands. Windows/Mac computers can join built-in streams but can't host them yet.
  - On Wayland your desktop shows its own "share a window" picker: choose the game window.
  - Every session writes what the guest experienced (round trip, jitter buffer, decode time,
    frames per second, lost packets) AND what the host spent (capture age, convert, encode, pack)
    to ~/.netplay-matchmaker/stream-stats.csv. Turn host timing off with --no-timing. The guest can show
    the same numbers on screen: click the little status pill (top right) or press F2.
    Those numbers are how we tune latency.
  - Players only pair if both use the same streaming method (Built-in or Parsec, Settings).
  - Behind a strict router the stream may not connect with only the free STUN server. The fix is
    a TURN relay: set NETPLAY_ICE_JSON on the matchmaker (see PROTOCOL.md).

LEGAL AND PRIVACY
  PRIVACY.md says exactly what leaves your device. NOTICE.md lists third-party licenses, the
  Retro-Netplay is licensed under GPL-3.0 (see LICENSE).

TESTING WITHOUT A SECOND PERSON OR YOUR HOME PC
  Read PHONE-TEST-GUIDE.txt. `sh phone-test.sh` starts the matchmaker in test mode (python3 desktop/server.py --demo)
  and a pretend opponent (desktop/fake_opponent.py). Open http://127.0.0.1:7778/demo to try the stream page.
  Never use --demo on a public matchmaker: it lets anyone create test sessions.

INVITE LINKS
  Starting a search makes an invite link on either app. Friends who open it join you in a private
  match; everyone else can still match you at random (switchable). The desktop app can also post
  the link to Discord or Telegram for you if you connect them (off until you turn it on). The
  Android app uses the share sheet, so every social app you have works and no login is stored.
  The matchmaker serves a small web page for these links on port 7778 (TCP port + 1), so allow
  that port as well as 7777 if you host it.

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
     Load Core > Download a Core > mGBA, PCSX ReARMed, Flycast. Streaming needs nothing extra: a web browser is enough.
  3. Open the app > Settings: type the matchmaker address (your PC's address) and port 7777.
  4. Add a game: its name (same as your opponent's), its system, and the full path to the game
     file, for example /storage/emulated/0/ROMs/game.gba. Leave the path blank for PS2 / PS3 games
     you'll only stream.
  5. Press "Find an opponent". The app opens RetroArch, or your browser for a stream; for RetroArch you finish the
     connection in its Netplay menu (the host's address is copied for you).

WHAT WAS AND WASN'T TESTED
  Tested: the matchmaker rules, the desktop app's engine, and the Android app's matching logic
  (it was run against the real server with the desktop app as the other player).
  Tested for the built-in streamer (tests in desktop/tests/): the guest web page in a real
  Chromium, over real WebRTC against a stand-in host: video arrives at 60 fps, controller/keyboard
  packets arrive intact, latency numbers are measured, access control (each side's own key), the
  host's session logic and the virtual-controller event translation.
  NOT tested: the real host (screen capture, GPU encoding, GStreamer, the virtual controller on a
  real /dev/uinput, the Wayland picker), so actual latency is unknown until you run it. Also not
  tested: the Android screens and the RetroArch launch (never built or run on a phone), the
  desktop windows on screen, and any real emulator session.
