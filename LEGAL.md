# Retro-Netplay: legal and compliance plan

This is the checklist the project follows as it's built, so publishing doesn't need a rewrite later. It is a practical engineering guide, not legal advice; get a lawyer's review before charging money or launching widely.

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
