package net.netplay.core;

import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Your identity and your friends. The secret never leaves the device except to the matchmaker you chose (which
 * hashes it into your id). Friends know only your id and name. Privileges are stored here and never sent anywhere,
 * except "priority", which the matchmaker needs to match you with that friend first. See PROTOCOL.md, Friends.
 */
public final class Friends {
    private Friends() { }

    public static final Pattern ID = Pattern.compile("^[0-9a-f]{24}$");
    public static final Pattern SECRET = Pattern.compile("^[0-9a-f]{32,64}$");
    private static final SecureRandom RNG = new SecureRandom();

    public static final class Friend {
        public final String id;
        public String name;
        /** Matched with them before anyone else. */
        public boolean priority;
        /** They can join your game without you accepting. */
        public boolean join;
        /** They can watch your games without you accepting. */
        public boolean watch;
        /** On a stream you host, they may pick Player 2, 3 or 4. */
        public boolean slot;

        public Friend(String id, String name) { this.id = id; this.name = cleanName(name).isEmpty() ? "Friend" : cleanName(name); }

        public Map<String, Object> toMap() {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("id", id); m.put("name", name);
            m.put("priority", priority); m.put("join", join); m.put("watch", watch); m.put("slot", slot);
            return m;
        }

        static Friend fromMap(Object o) {
            if (!(o instanceof Map)) return null;
            Map<?, ?> m = (Map<?, ?>) o;
            Object id = m.get("id");
            if (!(id instanceof String) || !ID.matcher((String) id).matches()) return null;
            Friend f = new Friend((String) id, m.get("name") instanceof String ? (String) m.get("name") : "Friend");
            f.priority = Boolean.TRUE.equals(m.get("priority"));
            f.join = Boolean.TRUE.equals(m.get("join"));
            f.watch = Boolean.TRUE.equals(m.get("watch"));
            f.slot = Boolean.TRUE.equals(m.get("slot"));
            return f;
        }
    }

    // ------------------------------------------------------------ identity
    public static String newSecret() {
        byte[] b = new byte[16];
        RNG.nextBytes(b);
        return hex(b);
    }

    /** Public id: first 24 hex digits of SHA-256 of the secret's text. Same as the server's player_id(). */
    public static String idOf(String secret) {
        try {
            MessageDigest d = MessageDigest.getInstance("SHA-256");
            return hex(d.digest(secret.getBytes(StandardCharsets.US_ASCII))).substring(0, 24);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format(Locale.ROOT, "%02x", x & 0xFF));
        return s.toString();
    }

    /** "a1b2c3d4e5f6..." -> "a1b2-c3d4-e5f6-..." so it's easier to read out loud. */
    public static String pretty(String id) {
        StringBuilder s = new StringBuilder();
        for (int i = 0; i < id.length(); i += 4) {
            if (s.length() > 0) s.append('-');
            s.append(id, i, Math.min(id.length(), i + 4));
        }
        return s.toString();
    }

    public static String cleanName(String n) {
        if (n == null) return "";
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < n.length(); i++) {
            char c = n.charAt(i);
            if (Character.isISOControl(c) || "<>\"\\".indexOf(c) >= 0) continue;
            int t = Character.getType(c);
            if (t == Character.FORMAT || t == Character.SURROGATE || t == Character.UNASSIGNED || t == Character.PRIVATE_USE) continue;
            b.append(Character.isWhitespace(c) ? ' ' : c);
        }
        String s = b.toString().trim().replaceAll(" {2,}", " ");
        return s.length() > 24 ? s.substring(0, 24).trim() : s;
    }

    // ------------------------------------------------------------ links
    /** The link you send to a friend: a web page on your matchmaker that opens the app. */
    public static String link(String host, int tcpPort, String id, String name) {
        try {
            return "http://" + host + ":" + (tcpPort + 1) + "/friend?i=" + id + "&n=" + URLEncoder.encode(cleanName(name), "UTF-8");
        } catch (java.io.UnsupportedEncodingException e) {
            throw new IllegalStateException(e);
        }
    }

    /** A friend link (web or netplay://), or a bare id typed in (dashes and spaces ignored). Null if neither. */
    public static Friend parse(String text) {
        if (text == null) return null;
        String t = text.trim();
        String bare = t.toLowerCase(Locale.ROOT).replaceAll("[\\s-]", "");
        if (ID.matcher(bare).matches()) return new Friend(bare, "Friend");
        try {
            URI u = new URI(t);
            boolean app = "netplay".equals(u.getScheme()) && "friend".equals(u.getHost() != null ? u.getHost() : u.getAuthority());
            boolean web = ("http".equals(u.getScheme()) || "https".equals(u.getScheme())) && "/friend".equals(u.getPath());
            if (!app && !web) return null;
            Map<String, String> q = query(u.getRawQuery());
            String id = q.getOrDefault("i", "");
            if (!ID.matcher(id).matches()) return null;
            return new Friend(id, q.getOrDefault("n", "Friend"));
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

    // ------------------------------------------------------------ the list
    @SuppressWarnings("unchecked")
    public static List<Friend> load(String json) {
        List<Friend> out = new ArrayList<>();
        try {
            for (Object o : (List<Object>) Json.parse(json == null || json.isEmpty() ? "[]" : json)) {
                Friend f = Friend.fromMap(o);
                if (f != null && find(out, f.id) == null) out.add(f);
            }
        } catch (RuntimeException ignored) { }
        return out;
    }

    public static String save(List<Friend> list) {
        List<Object> l = new ArrayList<>();
        for (Friend f : list) l.add(f.toMap());
        return Json.stringify(l);
    }

    public static Friend find(List<Friend> list, String id) {
        for (Friend f : list) if (f.id.equals(id)) return f;
        return null;
    }

    public static List<String> ids(List<Friend> list) {
        List<String> out = new ArrayList<>();
        for (Friend f : list) out.add(f.id);
        return out;
    }

    public static List<String> priorityIds(List<Friend> list) {
        List<String> out = new ArrayList<>();
        for (Friend f : list) if (f.priority) out.add(f.id);
        return out;
    }

    /** Search fields for the matchmaker (PROTOCOL.md, Friends). preferHost: you are already in the game. */
    public static Map<String, Object> searchFields(String secret, String name, List<Friend> list, boolean friendsOnly,
                                                   int friendWait, boolean preferHost) {
        Map<String, Object> m = new LinkedHashMap<>();
        if (secret != null && SECRET.matcher(secret).matches()) {
            m.put("me", secret);
            m.put("name", cleanName(name));
            m.put("friends", ids(list));
            m.put("priority", priorityIds(list));
            m.put("friend_wait", (long) friendWait);
            m.put("friends_only", friendsOnly);
        }
        m.put("prefer_host", preferHost);
        return m;
    }
}
