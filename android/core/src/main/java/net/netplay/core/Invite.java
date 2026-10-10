package net.netplay.core;

import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Pattern;

/** Invite links. Must stay compatible with the desktop app's invites.py (see PROTOCOL.md). */
public final class Invite {
    private static final String ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    private static final Pattern CODE = Pattern.compile("^[A-Za-z0-9]{4,32}$");
    private static final Pattern GAME = Pattern.compile("^[a-z0-9]+:[a-z0-9-]+$");
    private static final Pattern HOST = Pattern.compile("^[A-Za-z0-9.\\-\\[\\]:]{1,255}$");
    private static final SecureRandom RNG = new SecureRandom();

    public final String host, game, code;
    public final int port;

    private Invite(String host, int port, String game, String code) {
        this.host = host; this.port = port; this.game = game; this.code = code;
    }

    public static String newCode() {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < 8; i++) b.append(ALPHABET.charAt(RNG.nextInt(ALPHABET.length())));
        return b.toString();
    }

    /** The web link people share: it opens a page on the matchmaker (HTTP port = TCP port + 1). */
    public static String link(String host, int tcpPort, String gameKey, String code) {
        try {
            return "http://" + host + ":" + (tcpPort + 1) + "/join?g=" + URLEncoder.encode(gameKey, "UTF-8")
                    + "&c=" + code + "&p=" + tcpPort;
        } catch (java.io.UnsupportedEncodingException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Accepts the web link or the netplay:// link. Returns null for anything else. */
    public static Invite parse(String text) {
        try {
            URI u = new URI(text == null ? "" : text.trim());
            Map<String, String> q = query(u.getRawQuery());
            String host, port;
            if ("netplay".equals(u.getScheme()) && "join".equals(u.getHost() != null ? u.getHost() : u.getAuthority())) {
                host = q.getOrDefault("host", "");
                port = q.getOrDefault("port", "");
            } else if (("http".equals(u.getScheme()) || "https".equals(u.getScheme())) && "/join".equals(u.getPath())) {
                host = u.getHost() == null ? "" : u.getHost().replaceAll("^\\[|\\]$", ""); // same as the desktop app
                port = q.containsKey("p") ? q.get("p") : (u.getPort() > 0 ? String.valueOf(u.getPort() - 1) : "");
            } else {
                return null;
            }
            String game = q.getOrDefault("g", ""), code = q.getOrDefault("c", "");
            if (!HOST.matcher(host).matches() || !port.matches("^[0-9]{1,5}$")) return null;
            int p = Integer.parseInt(port);
            if (p < 1 || p > 65535 || !GAME.matcher(game).matches() || !CODE.matcher(code).matches()) return null;
            return new Invite(host, p, game, code);
        } catch (java.net.URISyntaxException | RuntimeException e) {
            return null;
        }
    }

    private static Map<String, String> query(String raw) {
        Map<String, String> m = new HashMap<>();
        if (raw == null) return m;
        for (String part : raw.split("&")) {
            int eq = part.indexOf('=');
            if (eq <= 0) continue;
            try {
                m.putIfAbsent(URLDecoder.decode(part.substring(0, eq), "UTF-8"), URLDecoder.decode(part.substring(eq + 1), "UTF-8"));
            } catch (java.io.UnsupportedEncodingException | IllegalArgumentException ignored) { }
        }
        return m;
    }
}
