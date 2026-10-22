package net.netplay.core;

import java.util.*;

/** Built-in-stream checks against a live matchmaker. Args: <port> <backend: own|parsec> [expect-no-match] */
public class StreamTest {
    public static void main(String[] a) throws Exception {
        int port = Integer.parseInt(a[0]);
        boolean parsec = a[1].equals("parsec");
        Games.Sys ps2 = Games.SYSTEMS.get("ps2");
        Plan.Offer offer = Plan.offer(ps2, false, false, false);
        Map<String, Object> info = new LinkedHashMap<>();
        info.put("native", new LinkedHashMap<String, Object>());
        info.put("stream", Plan.streamInfo(parsec));
        final MatchClient mc = new MatchClient();
        if (parsec || a.length > 2) {   // should never be paired (Parsec vs built-in, or an old host): wait, then give up
            new Thread(() -> { try { Thread.sleep(2500); } catch (InterruptedException e) { } mc.cancel(); }).start();
        }
        try {
            MatchClient.Match m = mc.search("127.0.0.1", port, Games.key("ps2", "Urban Reign"), offer.lanes, info, offer.canHost, "android");
            Plan plan = Plan.make(ps2, m.lane, m.role, m.peer, "tcp".equals(m.sig));
            System.out.println("LANE=" + m.lane);
            System.out.println("ROLE=" + m.role);
            System.out.println("KEYLEN=" + m.sessionKey.length());
            System.out.println("PLAN=" + plan.kind);
            System.out.println("URL=" + plan.clipboard);
            System.out.println("SIG=" + m.sig);
            System.out.println("PEERCLIENT=" + m.peerString("client"));
        } catch (MatchClient.Cancelled c) {
            System.out.println("NOMATCH");
        }
        System.out.println("DONE");
    }
}
