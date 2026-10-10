package net.netplay.core;

import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.*;

/** Plain main()-based checks (no JUnit needed). Args: <port> <slug-vectors-file>. */
public class CoreTest {
    static int failures = 0;

    static void check(String what, boolean ok) {
        System.out.println((ok ? "PASS " : "FAIL ") + what);
        if (!ok) failures++;
    }

    static boolean throwsIllegal(String json) {
        try { Json.parse(json); return false; } catch (IllegalArgumentException e) { return true; }
    }

    public static void main(String[] a) throws Exception {
        int port = Integer.parseInt(a[0]);

        // ---- Json
        Map<String, Object> m = Json.parseObject("{\"op\":\"matched\",\"v\":1,\"peer\":{\"ip\":\"1.2.3.4\",\"n\":[1,2.5,true,null,\"x\\n\\u00e9\"]}}");
        check("json nested parse", "matched".equals(m.get("op")) && Long.valueOf(1).equals(m.get("v")));
        String again = Json.stringify(m);
        check("json round trip", Json.parseObject(again).equals(m));
        check("json escapes", "a\"b\\c\n".equals(Json.parse(Json.stringify("a\"b\\c\n"))));
        check("json rejects garbage", throwsIllegal("{\"a\":") && throwsIllegal("nope") && throwsIllegal("{} extra"));

        // ---- game key matches the Python desktop app
        List<String> lines = Files.readAllLines(Paths.get(a[1]));
        int bad = 0;
        for (String l : lines) {
            String[] p = l.split("\t", -1);
            if (!Games.slug(p[0]).equals(p[1])) { bad++; System.out.println("  slug mismatch: [" + p[0] + "] java=" + Games.slug(p[0]) + " py=" + p[1]); }
        }
        check("slug identical to desktop for " + lines.size() + " names", bad == 0);

        // ---- Plan
        Games.Sys gba = Games.SYSTEMS.get("gba"), ps2 = Games.SYSTEMS.get("ps2");
        Plan.Offer o1 = Plan.offer(gba, true, true, false);
        check("gba offers native+stream, phone can't host either", o1.lanes.equals(Arrays.asList("native", "stream")) && !o1.canHost.get("native") && !o1.canHost.get("stream"));
        check("no RetroArch -> stream only", Plan.offer(gba, false, true, false).lanes.equals(Arrays.asList("stream")));
        check("no ROM path -> stream only", Plan.offer(gba, true, false, true).lanes.equals(Arrays.asList("stream")));
        check("ps2 is stream-only", Plan.offer(ps2, true, true, true).lanes.equals(Arrays.asList("stream")));
        check("user can host -> native hostable", Plan.offer(gba, true, true, true).canHost.get("native"));
        Map<String, Object> peer = new LinkedHashMap<>();
        peer.put("ip", "9.9.9.9"); peer.put("peer_id", "PID");
        Plan g = Plan.make(gba, "native", "guest", peer);
        check("native guest -> RetroArch, copies host ip", g.kind == Plan.Kind.LAUNCH_RETROARCH && g.clipboard.equals("9.9.9.9") && g.core.equals("mgba"));
        check("stream guest -> Parsec with peer id", Plan.make(ps2, "stream", "guest", peer).kind == Plan.Kind.OPEN_PARSEC);
        check("stream host is refused on Android", Plan.make(ps2, "stream", "host", peer).kind == Plan.Kind.ERROR);
        check("stream guest without peer id is an error", Plan.make(ps2, "stream", "guest", new HashMap<String, Object>()).kind == Plan.Kind.ERROR);

        // ---- cancel
        final MatchClient c1 = new MatchClient();
        final Object[] res = new Object[1];
        Thread t = new Thread(() -> {
            try { c1.search("127.0.0.1", port, Games.key("gba", "Never Matches"), Arrays.asList("native"), new LinkedHashMap<>(), new LinkedHashMap<>(), "android"); res[0] = "matched?!"; }
            catch (MatchClient.Cancelled e) { res[0] = "cancelled"; }
            catch (Exception e) { res[0] = e.toString(); }
        });
        t.start(); Thread.sleep(400); c1.cancel(); t.join(3000);
        check("cancel interrupts a waiting search", "cancelled".equals(res[0]));

        // ---- unreachable server
        try { new MatchClient().search("127.0.0.1", 1, "gba:x", Arrays.asList("native"), new LinkedHashMap<>(), new LinkedHashMap<>(), "android"); check("unreachable server throws", false); }
        catch (java.io.IOException e) { check("unreachable server throws IOException", true); }

        // ---- cross-app match: this phone waits (can't host); the Python DESKTOP app arrives later
        MatchClient c2 = new MatchClient();
        Map<String, Object> info = new LinkedHashMap<>();
        Map<String, Boolean> canHost = new LinkedHashMap<>();
        canHost.put("native", false); canHost.put("stream", false);
        System.out.println("waiting for the desktop app (server pings every 0.3s in this test)...");
        MatchClient.Match mt = c2.search("127.0.0.1", port, Games.key("gba", "Cross Test"), Arrays.asList("native", "stream"), info, canHost, "android");
        check("matched the desktop app on native as guest", mt.lane.equals("native") && mt.role.equals("guest"));
        check("peer says desktop-linux/other desktop client", mt.peerString("client").startsWith("desktop-"));
        check("peer ip provided for --connect", mt.peerString("ip").equals("127.0.0.1"));

        // ---- invite links: identical to the desktop app's invites.py
        int badLinks = 0, n = 0;
        for (String l : Files.readAllLines(Paths.get(a[2]))) {
            String[] p = l.split("\t", -1);
            n++;
            if (p[0].equals("BUILD")) {
                String got = Invite.link(p[1], Integer.parseInt(p[2]), p[3], p[4]);
                if (!got.equals(p[5])) { badLinks++; System.out.println("  build mismatch: " + got + " vs " + p[5]); }
            } else {
                Invite inv = Invite.parse(p[0]);
                String got = inv == null ? "NONE" : inv.host + "|" + inv.port + "|" + inv.game + "|" + inv.code;
                if (!got.equals(p[1])) { badLinks++; System.out.println("  parse mismatch: [" + p[0] + "] java=" + got + " py=" + p[1]); }
            }
        }
        check("invite links identical to desktop for " + n + " cases", badLinks == 0);
        String code = Invite.newCode();
        check("new invite codes: 8 chars, no look-alike characters", code.length() == 8 && code.matches("^[A-HJ-NP-Z2-9]{8}$"));

        // ---- a friend follows a link the DESKTOP app shared (private room, invite-only)
        String link = new String(Files.readAllBytes(Paths.get(a[3]))).trim();
        Invite inv = Invite.parse(link);
        check("parsed the desktop's invite link", inv != null && inv.game.equals(Games.key("gba", "Invite Test")));
        Map<String, Boolean> ch = new LinkedHashMap<>();
        ch.put("native", false); ch.put("stream", false);
        MatchClient.Match im = new MatchClient().search("127.0.0.1", inv.port, inv.game, Arrays.asList("native", "stream"),
                new LinkedHashMap<String, Object>(), ch, "android", inv.code, true);
        check("friend matched the desktop host through the invite", im.via.equals("invite") && im.role.equals("guest") && im.lane.equals("native"));

        // ---- a wrong code never matches
        final MatchClient wrong = new MatchClient();
        final Object[] r2 = new Object[1];
        Thread tw = new Thread(() -> {
            try { wrong.search("127.0.0.1", port, inv.game, Arrays.asList("native"), new LinkedHashMap<String, Object>(), new LinkedHashMap<String, Boolean>(), "android", "NOSUCH77", true); r2[0] = "matched?!"; }
            catch (MatchClient.Cancelled e) { r2[0] = "waited"; }
            catch (Exception e) { r2[0] = e.toString(); }
        });
        tw.start(); Thread.sleep(500); wrong.cancel(); tw.join(3000);
        check("wrong invite code finds nobody", "waited".equals(r2[0]));

        System.out.println(failures == 0 ? "ALL PASSED" : failures + " FAILED");
        System.exit(failures == 0 ? 0 : 1);
    }
}
