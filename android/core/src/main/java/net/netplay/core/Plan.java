package net.netplay.core;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Pure decisions: what to offer the matchmaker, and what to do when matched. No Android classes here. */
public final class Plan {
    public enum Kind { LAUNCH_RETROARCH, OPEN_PARSEC, OPEN_STREAM, ERROR }

    public final Kind kind;
    public final String message;
    public final String clipboard;   // text to copy for the user, or ""
    public final String core;        // RetroArch core name, or ""

    private Plan(Kind kind, String message, String clipboard, String core) {
        this.kind = kind; this.message = message; this.clipboard = clipboard; this.core = core;
    }

    /** Which lanes this phone can offer, and whether it can host each. */
    public static final class Offer {
        public final List<String> lanes = new ArrayList<>();
        public final Map<String, Boolean> canHost = new LinkedHashMap<>();
        public String problem = "";
    }

    public static Offer offer(Games.Sys sys, boolean retroArchInstalled, boolean hasRomPath, boolean userCanHostNative) {
        Offer o = new Offer();
        if (sys.hasNative() && retroArchInstalled && hasRomPath) {
            o.lanes.add("native");
            o.canHost.put("native", userCanHostNative);
        }
        // A phone can always join someone else's Parsec stream, but can never host one.
        o.lanes.add("stream");
        o.canHost.put("stream", false);
        return o;
    }

    /** What to say about our stream lane. Players only pair if they use the same streaming method. */
    public static Map<String, Object> streamInfo(boolean useParsec) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("backend", useParsec ? "parsec" : "own");
        return m;
    }

    /** Address of the guest page for a built-in stream match: the matchmaker's web port is its TCP port + 1. */
    public static String playUrl(String host, int port, String play) {
        if (play == null || play.isEmpty()) return "";
        return "http://" + (host.contains(":") && !host.startsWith("[") ? "[" + host + "]" : host) + ":" + (port + 1) + play;
    }

    public static Plan make(Games.Sys sys, String lane, String role, Map<String, Object> peer) {
        return make(sys, lane, role, peer, "");
    }

    /** playUrl: the full guest-page address when the match is on the built-in stream, else "". */
    public static Plan make(Games.Sys sys, String lane, String role, Map<String, Object> peer, String playUrl) {
        if ("stream".equals(lane) && playUrl != null && !playUrl.isEmpty()) {
            if (!"guest".equals(role)) {
                return new Plan(Kind.ERROR, "This phone can't host a stream, so this match can't start. Please search again.", "", "");
            }
            return new Plan(Kind.OPEN_STREAM, "Opening the stream in your browser. Pair a Bluetooth controller first, or use the on-screen buttons.", playUrl, "");
        }
        String ip = peer.get("ip") == null ? "" : String.valueOf(peer.get("ip"));
        String pid = peer.get("peer_id") == null ? "" : String.valueOf(peer.get("peer_id"));
        if ("stream".equals(lane)) {
            if (!"guest".equals(role)) {
                return new Plan(Kind.ERROR, "Android can't host a Parsec stream, so this match can't start. Please search again.", "", "");
            }
            if (pid.isEmpty()) {
                return new Plan(Kind.ERROR, "Your opponent didn't share a Parsec ID, so the stream can't start.", "", "");
            }
            return new Plan(Kind.OPEN_PARSEC, "Opening Parsec. If it doesn't connect, join peer ID " + pid + " in the Parsec app (copied).", pid, "");
        }
        if (!sys.hasNative()) {
            return new Plan(Kind.ERROR, sys.label + " netplay isn't available on Android.", "", "");
        }
        if ("host".equals(role)) {
            return new Plan(Kind.LAUNCH_RETROARCH,
                    "RetroArch is opening. Once the game runs, open RetroArch's Netplay menu and choose to host. Your opponent will connect to you.",
                    "", sys.core);
        }
        return new Plan(Kind.LAUNCH_RETROARCH,
                "RetroArch is opening. Once the game runs, open RetroArch's Netplay menu, choose to connect, and enter " + ip + " (copied).",
                ip, sys.core);
    }
}
