# Notices and licenses

## This project
No license has been chosen for Retro-Netplay's own code yet, so all rights are reserved. LEGAL.md recommends GPL-3.0 because the emulators it works with are GPL. The owner should pick one and add a `LICENSE` file before others use or modify the code.

## Provided "as is"
The software has no warranty. Use it at your own risk. Streaming and virtual controllers can misbehave; save your games.

## Games and consoles
This project contains no games, BIOS files or console firmware. You must own the games you play and follow your local copyright law. It is not affiliated with or endorsed by Sony, Nintendo, Sega, Microsoft, NVIDIA, Parsec, RetroArch/Libretro, Dolphin, PCSX2, RPCS3 or Discord, whose names are trademarks of their owners and are used only to say what the software works with.

## Software this project uses (not included in the package unless noted)
| Component | How it is used | License (check each project for the current terms) |
|---|---|---|
| Python and tkinter | runs the desktop app | Python Software Foundation License |
| pygame | installed on first start by `start.sh` for controller input | LGPL 2.1 |
| GStreamer and plugins, PyGObject | used from the host's own system for streaming | LGPL (some plugin sets, such as x264 in plugins-ugly, carry other licenses: check your distro's packages) |
| Android SDK, Gradle, Android Gradle Plugin | build the Android app (GitHub Actions or your machine) | Apache 2.0 (SDK components have their own terms) |
| Chromium, Playwright | used only by the automated tests in `desktop/tests/` | BSD-style / Apache 2.0 |
| RetroArch and libretro cores | started by the app | GPLv3 and per-core licenses |
| Dolphin, PCSX2, RPCS3 | started by the app | GPLv2+ / GPLv3 |
| Google STUN server | optional default for connecting through routers (see PRIVACY.md) | a public service, no software included |

The Android app and desktop app only launch the emulators above as separate programs; no emulator code is part of this package.

This notice is a good-faith summary, not legal advice. Have a lawyer review it before commercial distribution.
