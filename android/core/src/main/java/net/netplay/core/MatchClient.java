package net.netplay.core;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** One blocking search against the matchmaker. Call from a background thread; cancel() from any thread. */
public final class MatchClient {
    public static final class Match {
        public final String lane, role, via;
        public final Map<String, Object> peer;
        /** Built-in stream only: this side's session key, and (guest) the page path to open. Both "" otherwise. */
        public final String sessionKey, play;
        /** True when the matchmaker's web port uses https (the server operator set up a certificate). */
        public final boolean secureWeb;
        Match(String lane, String role, String via, Map<String, Object> peer, String sessionKey, String play) {
            this(lane, role, via, peer, sessionKey, play, false);
        }
        Match(String lane, String role, String via, Map<String, Object> peer, String sessionKey, String play, boolean secureWeb) {
            this.lane = lane; this.role = role; this.via = via; this.peer = peer; this.sessionKey = sessionKey; this.play = play;
            this.secureWeb = secureWeb;
        }
        public String peerString(String key) {
            Object v = peer.get(key);
            return v == null ? "" : String.valueOf(v);
        }
    }

    public static final class Cancelled extends IOException {
        private static final long serialVersionUID = 1L;
        public Cancelled() { super("search cancelled"); }
    }

    private volatile Socket socket;
    private volatile boolean cancelled;

    public void cancel() {
        cancelled = true;
        Socket s = socket;
        if (s != null) {
            try { s.close(); } catch (IOException ignored) { }
        }
    }

    public Match search(String host, int port, String game, List<String> lanes,
                        Map<String, Object> info, Map<String, Boolean> canHost, String client) throws IOException {
        return search(host, port, game, lanes, info, canHost, client, null, false);
    }

    /** invite = private-room code (or null). inviteOnly = wait ONLY in that room (what a friend following a link does). */
    public Match search(String host, int port, String game, List<String> lanes,
                        Map<String, Object> info, Map<String, Boolean> canHost, String client,
                        String invite, boolean inviteOnly) throws IOException {
        Socket s = new Socket();
        socket = s;
        try {
            if (cancelled) throw new Cancelled();
            s.connect(new InetSocketAddress(host, port), 8000);
            s.setKeepAlive(true);
            Map<String, Object> req = new LinkedHashMap<>();
            req.put("v", 1L);
            req.put("op", "search");
            req.put("game", game);
            req.put("lanes", lanes);
            req.put("info", info);
            req.put("can_host", canHost);
            req.put("client", client);
            if (invite != null && !invite.isEmpty()) {
                req.put("invite", invite);
                req.put("invite_only", inviteOnly);
            }
            OutputStream out = s.getOutputStream();
            out.write((Json.stringify(req) + "\n").getBytes(StandardCharsets.UTF_8));
            out.flush();
            BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
            while (true) {
                String line = in.readLine();
                if (line == null) throw new IOException("the matchmaker hung up");
                Map<String, Object> m = Json.parseObject(line);
                Object op = m.get("op");
                if ("ping".equals(op)) continue;
                if ("error".equals(op)) throw new IOException(String.valueOf(m.get("msg")));
                if ("matched".equals(op)) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> peer = m.get("peer") instanceof Map ? (Map<String, Object>) m.get("peer") : new LinkedHashMap<String, Object>();
                    String key = "", play = "";
                    boolean https = false;
                    if (m.get("session") instanceof Map) {
                        Map<?, ?> sess = (Map<?, ?>) m.get("session");
                        key = sess.get("key") == null ? "" : String.valueOf(sess.get("key"));
                        play = sess.get("play") == null ? "" : String.valueOf(sess.get("play"));
                        https = "https".equals(sess.get("web"));
                    }
                    return new Match(String.valueOf(m.get("lane")), String.valueOf(m.get("role")), m.get("via") == null ? "public" : String.valueOf(m.get("via")), peer, key, play, https);
                }
            }
        } catch (IOException e) {
            if (cancelled) throw new Cancelled();
            throw e;
        } finally {
            try { s.close(); } catch (IOException ignored) { }
        }
    }
}
