# Retro-Netplay: legal and compliance plan

This is the checklist the project follows as it's built, so publishing doesn't need a rewrite later. It is a practical engineering guide, not legal advice, and no one can promise a project will never be sued. Get a lawyer's review before charging money or launching widely. Last reviewed 10 October 2026.

## 1. Emulator licenses (decide before writing integration code)

| Emulator | License |
|---|---|
| PCSX2 | GPL-3.0-or-later |
| Dolphin | GPL-2.0-or-later |
| RPCS3 | GPL-2.0 |

- **If Retro-Netplay bundles, modifies or links against any of these**, the combined app must be distributed under a compatible GPL license (GPL-3.0 covers all three), with full source available.
- **If it only launches emulators the user installed separately** (separate process, talking over sockets/CLI/netplay ports), the app can use any license, but must not ship their binaries without also meeting GPL source-offer terms.
- Recommendation: license Retro-Netplay as **GPL-3.0** from the start. That keeps every integration option open and matches the emulator ecosystem.
- Keep a `THIRD_PARTY_NOTICES` file listing every dependency, its license and copyright line, and ship it with each release (desktop and Android).

## 2. Games, BIOS and firmware

- Never ship, download, host or link to ROMs, ISOs, BIOS or firmware files (PS2 BIOS, PS3 firmware, Dreamcast BIOS, GBA BIOS and so on). These are copyrighted.
- The app asks the user to point to files they own and dumped themselves; the UI and docs say so.
- Prefer open replacements where they exist (for example, HLE BIOS modes).
- Don't add features whose main purpose is getting games (built-in ROM browsers, "download game" buttons).

## 3. Trademarks

- Don't use console makers' or Parsec's logos, and don't imply endorsement. Plain text like "works with PCSX2" or "PS2 games via PCSX2" is fine.
- No box art or official game artwork in the app, store listings or site. Game titles in plain text are fine.
- Before a public launch, search the "Retro-Netplay" name in the USPTO trademark search and the Play Store.

## 4. Streaming and playing with strangers

- Sessions stream gameplay only to the people in that session. Don't add public broadcast or recording of other people's sessions without their consent.
- Matchmaking with "randoms" needs: block and report, a way to leave instantly, and no open text or voice chat with strangers until there's moderation.
- Games draw a young audience. Either set a 13+ age requirement (with an age prompt before random matchmaking), or plan for COPPA if children under 13 can use online features.

## 5. Privacy

- **Peer-to-peer exposes IP addresses** to the other players. Say so plainly in the app before the first online session, and offer relay mode as a privacy option.
- Any matchmaking or relay server: log only what's needed to run it, keep it short (for example, 30 days), and document it.
- Invite links: random, unguessable, and expire when the session ends.
- Social account connections: request the smallest scopes needed to post an invite. Follow each platform's developer terms (Discord, X and so on); each one requires a privacy policy URL. Never post without the user confirming.
- Publish a **Privacy Policy** and **Terms of Use** (hosted page, linked from the app and store listing) before the first public release.
- Google Play requires a privacy policy link and an accurate **Data safety** form.

## 6. Privacy by design (build rules)

These keep Retro-Netplay as private as possible from day one.

- **Collect nothing by default.** No accounts, analytics, crash reporting or telemetry unless the user opts in, and every opt-in is off by default.
- **Relay mode by default for random matchmaking**, so strangers never see your IP address; direct peer-to-peer is opt-in (or the default only for friend invites, with a clear notice).
- **End-to-end encrypt game streams and inputs** between players (for example DTLS/SRTP or Noise). Relay and matchmaking servers forward encrypted traffic and can't see the game, voice or inputs.
- **Matchmaking servers keep the minimum:** a random session ID and connection details, held in memory and dropped when the session ends. No IP logs beyond short-lived abuse protection (24–72 hours).
- **Invite links** use long random tokens, expire when the session ends, and contain no username or IP.
- **Social account connections are optional.** Ask for the smallest permissions (post a message only), store tokens in the OS keychain/Android Keystore, never on a server, and let users disconnect in one tap.
- **Local data** (settings, game paths, recent friends) stays on the device. Offer "Delete all my data" in settings.
- **No third-party SDKs** that phone home (ads, analytics, attribution). Bundle libraries and fonts in the app instead of loading them from CDNs.
- **Android:** request only the permissions needed (no location, contacts or storage-wide access; use the system file picker for game files). The Play Store Data safety form must say "no data collected" where true.
- **Updates** are signed and downloaded over HTTPS from the project's own release page.

## 7. Release checklist

- [ ] LICENSE (GPL-3.0) and THIRD_PARTY_NOTICES in the repo and in every build
- [ ] Source code available for every released binary
- [ ] No BIOS, firmware or game files anywhere in the repo, builds or downloads
- [ ] Privacy Policy and Terms of Use published and linked in the app
- [ ] IP-exposure notice and consent before the first online session
- [ ] Age requirement and block/report in random matchmaking
- [ ] Play Store Data safety form matches what the app really collects
- [ ] Trademark name search done
- [ ] Privacy-by-design rules in section 6 met (relay default for randoms, encryption, no telemetry, data deletion)

## 8. Streaming a game to another person (the biggest open question)

The built-in streamer sends video and sound of a running game from the host's computer to a guest's browser. That is different from two people each running their own copy in sync (netplay).

- Sending a copyrighted game's picture and sound to someone else can be treated as a public performance or distribution, depending on the country. Platforms such as Steam and Parsec handle this with their own licences and terms; a hobby project has none.
- Mitigations the design already uses: private sessions between two people, no public or broadcast mode, no recording, invite links that expire.
- Keep doing: tell both players that they should each own the game, never offer "watch" or "spectate" links for strangers, and never record or re-stream sessions.
- Have a lawyer look at this section before any public launch. It is the part most likely to attract a complaint from a publisher.

## 9. Circumvention and emulator-adjacent law

- US copyright law (DMCA section 1201) bans circumventing technical protection. Don't add features that decrypt discs, dump BIOS or firmware from consoles, or bypass copy protection. Point users to dumping guides from the emulator projects themselves instead.
- Nintendo v. Yuzu (2024 settlement) shows publishers will sue emulator developers over encryption keys and in-app instructions for getting them. Keep this project a *launcher and matchmaker* for emulators the user installs, and never ship keys, firmware or links to them.
- Don't patch or bundle emulators with game-specific hacks that bypass online-service checks of the original games.

## 10. Running a matchmaker or relay for other people

Running a public server makes you responsible for it.

- Publish PRIVACY.md and TERMS.md (in this repo) at the matchmaker's address; log nothing but short-lived abuse protection (see section 6).
- Provide an abuse contact and respond to reports. Remove abusive users' access quickly.
- Never run `--demo` on a public server.
- If you add accounts, chat or voice, redo sections 4 and 5 (moderation, age, data retention, reporting duties for illegal content).
- A TURN relay carries other people's traffic; rate-limit it and restrict it to authenticated sessions so it can't be used for attacks or anonymous proxying.

## 11. Distribution and platform rules

- **Android developer verification:** Google's programme for apps installed outside Google Play begins 30 September 2026 in Brazil, Indonesia, Singapore and Thailand, with a global rollout from 2027. Free "limited distribution" accounts exist for hobbyists sharing with up to 20 devices. Register before public release. https://developer.android.com/developer-verification
- **Google Play:** emulator-related apps are allowed but must not facilitate piracy; Play may reject apps that mention ROM downloads. Keep store text free of "free games" language.
- **Encryption export rules:** apps using standard encryption (WebRTC/DTLS) are generally covered by open-source and mass-market exceptions in the US (EAR); keep source public and check current rules before commercial distribution.
- **EU Cyber Resilience Act:** non-commercial open-source hobby projects are largely outside its scope; selling the app or running it as a paid service can change that.
- **Accessibility:** remappable controls, readable text sizes, and screen-reader labels reduce complaint risk (ADA/EAA) and help users.

## 12. Documents in this repository

| File | Purpose |
|---|---|
| LEGAL.md | this plan |
| PRIVACY.md | what the software shares and with whom |
| TERMS.md | rules for using the software and any matchmaker |
| NOTICE.md | licences, trademarks, warranty |
| SECURITY.md | how to report vulnerabilities |

**Open decision for the owner:** choose the project's own licence (GPL-3.0 recommended in section 1) and add a `LICENSE` file. Until then the code is all rights reserved and others cannot legally reuse it.
