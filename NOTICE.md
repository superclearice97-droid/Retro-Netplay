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
| GStreamer and plugins, PyGObject | hosting a stream: used from the host's own system. Joining: **included** in the Linux, Windows and Mac downloads inside the player window (`_internal/player`; on Mac `Retro-Netplay.app/Contents/Resources/player`), only the plugins it needs | LGPL 2.1+ (GStreamer, GLib, PyGObject); some plugin sets carry other licenses, e.g. x264 in plugins-ugly (GPL), which the download does NOT include |
| libnice, libsrtp2, usrsctp (in GStreamer's sctp plugin), OpenSSL, GnuTLS, Opus and the other libraries GStreamer needs | **included** in the player window (WebRTC connection, encryption, sound) | LGPL 2.1 / MPL 1.1 (libnice), BSD (libsrtp2, usrsctp, Opus), Apache 2.0 (OpenSSL 3), LGPL 2.1+ (GnuTLS); every bundled library's own licence text is in `_internal/player/licenses/` |
| PySDL2, SDL2, SDL2_ttf (with FreeType and HarfBuzz) | the player window: picture, keyboard and controllers; **included** (pysdl2-dll binaries) | PySDL2 public domain / zlib; SDL2 and SDL2_ttf zlib; FreeType FTL; HarfBuzz MIT |
| stream-webrtc-android 1.2.2 (a build of Google's WebRTC), with BoringSSL, libvpx, libaom, dav1d, Opus, libsrtp, libyuv, Protocol Buffers, libevent, Abseil inside it; Kotlin standard library, AndroidX annotation | the Android app's stream player; **included** in the Android app | Apache 2.0 (stream-webrtc-android, Abseil, Kotlin, AndroidX); WebRTC BSD 3-clause with Google's patent grant; BoringSSL OpenSSL/SSLeay/ISC; libvpx BSD with the WebM patent grant; libaom BSD 2-clause with the AOM Patent License 1.0; dav1d BSD 2-clause; Opus, libsrtp, libyuv, Protocol Buffers, libevent BSD 3-clause. Full texts ship inside the app: `assets/THIRD-PARTY-NOTICES.txt` (android/app/src/main/assets/) |
| Android SDK, Gradle, Android Gradle Plugin | build the Android app (GitHub Actions or your machine) | Apache 2.0 (SDK components have their own terms) |
| Chromium, Playwright, coturn | used only by the automated tests in `desktop/tests/` | BSD-style / Apache 2.0 / BSD |
| coturn (TURN relay) | optional, run by whoever runs a matchmaker (not included) | BSD 3-clause |
| RetroArch and libretro cores | started by the app | GPLv3 and per-core licenses |
| Dolphin, PCSX2, RPCS3 | started by the app | GPLv2+ / GPLv3 |
| Google STUN server | optional default for connecting through routers (see PRIVACY.md) | a public service, no software included |

The Android app and desktop app only launch the emulators above as separate programs; no emulator code is part of this package.

In the Linux download, pygame/SDL2 and the other libraries are separate shared library files inside the app's `_internal` folder, so you can replace them with your own builds as the LGPL allows. The same goes for the player window's GStreamer and its libraries in `_internal/player/_internal`. On Linux those are Ubuntu 22.04's own builds, unchanged; on Windows they are MSYS2's builds (source: https://packages.msys2.org/base/<package>, versions in the same PACKAGES.txt, licence texts next to it); on Mac they are Homebrew's builds, with SDL2 and SDL2_ttf from the pysdl2-dll package as on Linux (formula and version in PACKAGES.txt, licence texts next to it, source: https://formulae.brew.sh/formula/<formula>). On Linux: `_internal/player/licenses/PACKAGES.txt` names each package and its exact version, and its source code is available with `apt source <package>=<version>` or from https://launchpad.net/ubuntu/+source/<package>. The source code of Retro-Netplay itself, for every download, is this repository at the commit named in the release notes.

**Video codecs.** The download contains no software H.264 video encoder or decoder (no x264, gst-libav/FFmpeg or OpenH264), because H.264 is covered by patent licensing. The player decodes the stream with your graphics card's own decoder (VA-API on Intel/AMD, NVIDIA's decoder, Direct3D on Windows, VideoToolbox on Mac), which comes with your graphics driver or operating system, or with the H.264 decoder your system's own GStreamer provides if you installed one (for example gstreamer1.0-libav). The host uses the encoder installed on its own system. The Android app likewise has no software H.264 decoder: its player decodes H.264 with the phone's own decoder (Android MediaCodec); WebRTC's VP8/VP9/AV1 decoders inside it are royalty-free codecs under their projects' patent grants. Opus (sound) is royalty-free.

This notice is a good-faith summary, not legal advice. Have a lawyer review it before commercial distribution.

## Look and fonts
The icons are our own drawings (design/make_icons.py); no third-party icon files are used. The Android app and the desktop player window bundle DejaVu Sans Mono, which is free to use and redistribute under the Bitstream Vera / DejaVu licence (android/app/src/main/assets/DejaVu-LICENSE.txt, desktop/fonts/DejaVu-LICENSE.txt). The rest of the desktop app and the browser pages use whatever monospace font your system already has.
