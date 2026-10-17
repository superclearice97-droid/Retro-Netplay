NETPLAY MATCHMAKER
Play retro games online. Press one button, get matched with an opponent.

It looks for an opponent two ways at once and uses whichever finds someone first:
  1. The emulator's own netplay (rollback, best feel)  - GBA, PS1, Dreamcast, GameCube/Wii
  2. A stream (backup)                                 - works for everything, incl. PS2/PS3
                                                          (built-in streamer, or Parsec; Settings > Streaming)

SETUP (about 5 minutes)
  1. Install what you need:
       RetroArch  - GBA, PS1, Dreamcast (then in RetroArch: Load Core > Download a Core,
                    and download mGBA, PCSX ReARMed and/or Flycast)
       Dolphin    - GameCube / Wii
       PCSX2      - PS2        RPCS3 - PS3
       PS2/PS3 streaming - nothing extra to install on Windows/Mac guests (a web browser is enough).
                    To HOST a stream from Linux run  python3 stream_host.py --doctor  and follow it.
                    (Parsec still works: pick it in Settings > Streaming.)
     You need your own game files.
  2. Linux (easiest): download Retro-Netplay-x86_64.AppImage from the project's Releases page and
       double-click it. If it doesn't open: right-click it > Properties > Permissions > "Allow executing
       file as program", then double-click again. Or download the .zip, unzip it, open the folder and
       double-click "Retro-Netplay". Nothing else to install: Python is inside.
     Linux from source: run  sh install-linux.sh  once (or  sh start.sh ).
     Windows: double-click "Start (Windows).bat". It needs Python from python.org first
       (tick "Add python.exe to PATH"). A one-click Windows download is coming next.
     RetroArch, Dolphin, PCSX2 or RPCS3 installed as a Flatpak are found automatically.
  3. The first time, a short setup opens: pick your game FOLDERS (every game inside is found and
     named for you, so it matches the other player's name), check your emulators, the online
     settings, and your controllers. It can also add Retro-Netplay to your app menu.
  4. Paste your Parsec peer ID in Settings only if you chose Parsec for Streaming.
  5. At home there's nothing to set up: computers and phones on the same Wi-Fi find each
     other by themselves (they all pick the same matchmaker). For friends in another house,
     one of you shares an internet address (Friends > My address...) and the host's router
     must let port 7777 (TCP) in.
  6. Double-click your game (or select it and click "Play"). RetroArch games start right away and you're queued; other games
     start when you're matched. Hold Start + Select for 3 seconds to join or leave the queue.

CONTROLLERS (2 to 4 players)
  - The first time you open the app it asks if your controllers are ready for 2 players.
    Say no and it shows a step-by-step manual. You can reopen it any time from
    Controllers... > Setup manual.
  - Player 1 and Player 2 are always on, so a second person can pick up a controller and
    join at any time. Player 3 and 4 are optional tick boxes.
  - Controllers... > Map... walks you through pressing each button (about 30 seconds).
    Skip it if your controller already works.
  - Playing with 3 or 4 people: map one controller, click Copy, then Paste on the others.
    "Copy Player 1 to everyone" does it in one click. The layout is copied, not the controller.
  - Button layouts are applied automatically to RetroArch games (GBA, PS1, Dreamcast).
    Dolphin, PCSX2 and RPCS3 keep their own controller settings: set them inside the emulator
    and make sure Port 1 and Port 2 use different controllers.

PHONES AND TABLETS
  - This is the computer version. The Android app (in the android folder next to this one) is
    a separate app that talks to the same matchmaker, so Android and computer players can
    match each other. See the README one folder up.

INVITE LINKS AND POSTING
  - When you press "Play" the app makes an invite link, copies it, and shows it under
    the button. Send it to friends anywhere. They tap it, it opens the app (or tells them how to
    get it), and they land in a private match with you. You stay open to random players too, unless
    you untick that in "Invites & social...".
  - The link has to use an address your friends can reach. Set it in "Invites & social..." (a domain
    name or your public IP). Anyone who has the link can see that address.
  - Posting for you: in "Invites & social..." paste a Discord webhook address (Channel settings >
    Integrations > Webhooks) and/or a Telegram bot token and chat id, then tick "Post automatically".
    It is OFF until you tick it. Only your invite message is posted, never more often than the
    wait time, and the "Send a test post" button shows what happens. The secrets are saved only on
    this device (in a private file). Anyone who has them can post as you.
  - X, Instagram, Facebook and similar don't allow apps to post for you without a registered
    developer app, so use "Copy link" and paste it there. On Android the share sheet does this.
  - Friend sent you a link? Click "Join with a link..." and paste it. On Linux, clicking a
    netplay:// link opens the app directly after you run install-linux.sh.

NOTES
  - Emulator netplay (RetroArch) also needs the host's router to allow port 55435.
    If that's a hassle, the Parsec backup needs no port setup.
  - Dolphin's netplay can't be started automatically. The app tells you what to click.
  - Controller hotkey: click "Set controller hotkey", hold your buttons together, let go.
    Then hold them for 1 second any time to start or cancel a search. Needs pygame
    (the Start file installs it).
  - Your settings are saved in a folder called .netplay-matchmaker in your home folder.
