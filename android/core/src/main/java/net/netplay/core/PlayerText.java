package net.netplay.core;

/** What the phone's player says when a stream stops, in plain words. Same texts as the desktop player (stream_guest.py). */
public final class PlayerText {
    private PlayerText() { }

    /** The host's goodbye: why = its reason ("" when the friend ended it on purpose). */
    public static String bye(Object why) {
        String w = why == null ? "" : String.valueOf(why).trim();
        while (w.endsWith(".")) w = w.substring(0, w.length() - 1);
        if (w.length() > 300) w = w.substring(0, 300);
        if (w.isEmpty()) return "Your friend ended the session.";
        return "The stream stopped on your friend's computer: " + w + ".";
    }

    /** why = a StreamSignal ending, or one of the player's own ("ice failed", "no host", "no video"). */
    public static String end(String why, boolean relay) {
        switch (why) {
            case "session closed":
            case "session ended":
                return "The stream has ended.";
            case "lost contact with the matchmaker":
                return "Lost contact with the matchmaker, so the stream ended. Check your internet connection and press Join again.";
            case "ice failed":
                return relay
                        ? "Couldn't connect to your friend's computer, not even through the relay server. "
                          + "One of your networks may block it (a work or school network often does)."
                        : "Couldn't connect to your friend's computer. One of your routers blocks direct connections, and this "
                          + "matchmaker has no relay server for that. Ask whoever runs the matchmaker to add one (README: "
                          + "\"TURN relay\"), or try from the same Wi-Fi.";
            case "no host":
                return "Your friend's computer didn't answer. Retro-Netplay must be open there with the game running. "
                        + "Press Join again.";
            case "no video":
                return relay
                        ? "The video hasn't arrived, not even through the relay server. One of your networks may block it "
                          + "(a work or school network often does)."
                        : "The video hasn't arrived: your two devices can't reach each other directly, and this matchmaker has no "
                          + "relay server for strict routers. Try the same Wi-Fi (not a guest network), or ask whoever runs the "
                          + "matchmaker to add one (README: \"TURN relay\").";
            default:
                return why;
        }
    }
}
