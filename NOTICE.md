# Notices and licenses

## This project
Retro-Netplay's own code is licensed under the GNU General Public License, version 3 (GPL-3.0). See LICENSE. You may use, change and share it under those terms; if you distribute it, you must make the source available under the same license.

## Provided "as is"
The software has no warranty. Use it at your own risk. Streaming and virtual controllers can misbehave; save your games.

## Games and consoles
This project contains no games, BIOS files or console firmware. You must own the games you play and follow your local copyright law. It is not affiliated with or endorsed by Sony, Nintendo, Sega, Microsoft, NVIDIA, Parsec, RetroArch/Libretro, Dolphin, PCSX2, RPCS3 or Discord, whose names are trademarks of their owners and are used only to say what the software works with.

## Software this project uses (not included in the package unless noted)
| Component | How it is used | License (check each project for the current terms) |
|---|---|---|
| Python and tkinter (Tcl/Tk) | runs the desktop app; **included** in the Linux download | Python Software Foundation License; Tcl/Tk BSD-style licence |
| pygame and SDL2 | controller input; **included** in the Linux download (installed by `start.sh` when run from source) | pygame LGPL 2.1 (source: https://github.com/pygame/pygame), SDL2 zlib licence |
| PyInstaller bootloader | packs the Linux download | GPL 2.0 with an exception that allows packing any program |
| AppImage runtime (appimagetool) | makes the single-file Linux download | MIT |
| GStreamer and plugins, PyGObject | used from the host's own system for streaming | LGPL (some plugin sets, such as x264 in plugins-ugly, carry other licenses: check your distro's packages) |
| Android SDK, Gradle, Android Gradle Plugin | build the Android app (GitHub Actions or your machine) | Apache 2.0 (SDK components have their own terms) |
| Chromium, Playwright | used only by the automated tests in `desktop/tests/` | BSD-style / Apache 2.0 |
| RetroArch and libretro cores | started by the app | GPLv3 and per-core licenses |
| Dolphin, PCSX2, RPCS3 | started by the app | GPLv2+ / GPLv3 |
| Google STUN server | optional default for connecting through routers (see PRIVACY.md) | a public service, no software included |

The Android app and desktop app only launch the emulators above as separate programs; no emulator code is part of this package.

In the Linux download, pygame/SDL2 and the other libraries are separate shared library files inside the app's `_internal` folder, so you can replace them with your own builds as the LGPL allows. The source code of Retro-Netplay itself, for every download, is this repository at the commit named in the release notes.

This notice is a good-faith summary, not legal advice. Have a lawyer review it before commercial distribution.

## Look and fonts
The icons are our own drawings (design/make_icons.py); no third-party icon files are used. The Android app bundles DejaVu Sans Mono, which is free to use and redistribute under the Bitstream Vera / DejaVu licence (assets/DejaVu-LICENSE.txt). The desktop and browser pages use whatever monospace font your system already has.
