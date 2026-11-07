# Retro-Netplay

Play your old couch co-op and versus games online, with friends or with anyone. Like an arcade, from home.

Open a **room** for the game you're playing and people in the lobby can ask to join. Or browse the **Arcade** and jump into someone else's room. Your controller works as Player 2, 3 or 4.

- **Netplay rooms** (Game Boy Advance, PS1 and Dreamcast through RetroArch; GameCube and Wii through Dolphin): each player runs the game on their own computer, kept in sync by the emulator. Smoothest, and each player needs their own copy. Retro-Netplay starts RetroArch connected for you, and fills in Dolphin's NetPlay window.
- **Stream rooms** (any game, including PS2 and PS3): players see the host's game window and control their own player, like Parsec Arcade. They need nothing but the app.
- **Lobbies:** any Retro-Netplay app can run one, and people join it with its address or an invite link. Rooms, chat and moderation live there; no account is needed.
- **Chat:** lobby and room chat with a swear-word filter, block and report. Whoever runs a lobby can mute, remove and ban.
- **Game pages:** box art, details and screenshots from the files Skraper, EmulationStation or RetroArch already made next to your games.

Downloads: Linux (AppImage or zip), Windows and Mac (early tests), and Android (joins rooms; hosting from a phone comes later). See [README.txt](README.txt) for a step-by-step guide.

## Legal

Retro-Netplay does not include or distribute any games, BIOS or firmware; use only games you own. Not affiliated with Sony, Nintendo, Sega, Parsec or any emulator project. For people aged 13 and over. See [TERMS.md](TERMS.md), [PRIVACY.md](PRIVACY.md), [NOTICE.md](NOTICE.md), [SECURITY.md](SECURITY.md) and the project's legal plan, [LEGAL.md](LEGAL.md).

## Status

Early test build. The desktop app is in `desktop/` (engine.py, the interface in `desktop/ui`, the window in `desktop/shell`), the Android app in `android/`. [PROTOCOL.md](PROTOCOL.md) describes the lobby. Licensed under GPL-3.0 (see LICENSE).
