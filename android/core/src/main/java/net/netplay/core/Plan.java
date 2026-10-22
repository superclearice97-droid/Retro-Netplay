package net.netplay.core;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/** Pure decisions: what to offer the matchmaker, and what to do when matched. No Android classes here. */
public final class Plan {
    public enum Kind { LAUNCH_RETROARCH, OPEN_PARSEC, OPEN_PLAYER, ERROR }

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
        if (!useParsec) m.put("sig", "tcp");   // stream setup over the matchmaker connection; plays in the app's own player
        return m;
    }

    // Anything a matchmaker or opponent sends us is checked before we open it, copy it or pass it to an app.
    static final Pattern PEER_ID_RE = Pattern.compile("^[A-Za-z0-9]{1,64}$");
    static final Pattern IP_RE = Pattern.compile("^([0-9]{1,3}(\\.[0-9]{1,3}){3}|[0-9A-Fa-f:.]{2,45})$");

    public static Plan make(Games.Sys sys, String lane, String role, Map<String, Object> peer) {
        return make(sys, lane, role, peer, false);
    }

    /** player: the match is a built-in stream whose key works on the stream connection (session sig "tcp"). */
    public static Plan make(Games.Sys sys, String lane, String role, Map<String, Object> peer, boolean player) {
        if ("stream".equals(lane) && player) {
            if (!"guest".equals(role)) {
                return new Plan(Kind.ERROR, "This phone can't host a stream, so this match can't start. Please search again.", "", "");
            }
            return new Plan(Kind.OPEN_PLAYER, "Opening the stream. Your controller works in it, or use the on-screen buttons.", "", "");
        }
        String ip = peer.get("ip") == null ? "" : String.valueOf(peer.get("ip"));
        String pid = peer.get("peer_id") == null ? "" : String.valueOf(peer.get("peer_id"));
        if (!IP_RE.matcher(ip).matches()) ip = "";
        if (!PEER_ID_RE.matcher(pid).matches()) pid = "";
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
        if ("guest".equals(role) && ip.isEmpty()) {
            return new Plan(Kind.ERROR, "The matchmaker didn't send a valid address for your opponent. Please search again.", "", "");
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
