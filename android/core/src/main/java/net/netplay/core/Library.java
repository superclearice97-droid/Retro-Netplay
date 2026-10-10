package net.netplay.core;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Game folders: the same naming and system rules as desktop/library.py, so a game found on a phone and the same
 * game found on a PC get the same key and can be matched. tests/test_library.py checks both agree.
 */
public final class Library {
    private Library() { }

    public static final Map<String, List<String>> EXT_SYSTEMS = new LinkedHashMap<>();
    static {
        put(".gba", "gba");
        put(".cue", "ps1"); put(".pbp", "ps1"); put(".m3u", "ps1");
        put(".gdi", "dreamcast"); put(".cdi", "dreamcast");
        put(".gcm", "gamecube"); put(".rvz", "gamecube"); put(".gcz", "gamecube"); put(".wbfs", "gamecube"); put(".ciso", "gamecube");
        put(".cso", "ps2");
        put(".iso", "ps2", "gamecube", "ps3");
        put(".chd", "ps1", "ps2", "dreamcast");
        put(".bin", "ps2", "ps3");
    }
    public static final List<String> EXT_RANK = Arrays.asList(".m3u", ".chd", ".pbp", ".cue", ".gdi", ".cdi", ".rvz", ".gcz",
            ".wbfs", ".ciso", ".gcm", ".cso", ".iso", ".bin", ".gba");
    private static final String[][] HINTS = {
            {"gba", "gba", "gameboy advance", "game boy advance", "gameboyadvance"},
            {"ps2", "ps2", "playstation 2", "playstation2", "pcsx2"},
            {"ps3", "ps3", "playstation 3", "playstation3", "rpcs3"},
            {"ps1", "ps1", "psx", "psone", "ps one", "playstation 1", "playstation1", "playstation"},
            {"dreamcast", "dreamcast", "dc"},
            {"gamecube", "gamecube", "game cube", "gc", "ngc", "wii", "dolphin"},
    };
    public static final int SNIFF_BYTES = 0x9318 + 0x100;

    private static void put(String ext, String... systems) { EXT_SYSTEMS.put(ext, Arrays.asList(systems)); }

    private static final Pattern BRACKETS = Pattern.compile("[\\(\\[\\{][^\\)\\]\\}]*[\\)\\]\\}]");
    // Android's regex engine rejects the Unicode-character-class flag (it threw on first use and crashed the app),
    // so whitespace and digits are spelled out to match Python's Unicode \s and \d on every platform.
    private static final String WS = "[\\t\\n\\u000B\\f\\r\\u001C-\\u001F \\u0085\\u00A0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000]";
    private static final String DIGIT = "\\p{Nd}";
    private static final Pattern NUMBERED = Pattern.compile("^" + WS + "*" + DIGIT + "{3,5}" + WS + "*-" + WS + "*");
    private static final Pattern SPACES = Pattern.compile(WS + "+");
    private static final Pattern ARTICLE = Pattern.compile("^(.*?), (The|A|An)(" + WS + "+-" + WS + "+.*)?$", Pattern.DOTALL);

    public static String ext(String filename) {
        int dot = filename.lastIndexOf('.');
        return dot > 0 ? filename.substring(dot).toLowerCase(Locale.ROOT) : "";
    }

    private static String strip(String s, String chars) {
        int a = 0, b = s.length();
        while (a < b && chars.indexOf(s.charAt(a)) >= 0) a++;
        while (b > a && chars.indexOf(s.charAt(b - 1)) >= 0) b--;
        return s.substring(a, b);
    }

    /** "Legend of Zelda, The - The Minish Cap (USA) [!].gba" -> "The Legend of Zelda - The Minish Cap". */
    public static String cleanName(String filename) {
        String base = filename;
        int slash = Math.max(base.lastIndexOf('/'), base.lastIndexOf('\\'));
        if (slash >= 0) base = base.substring(slash + 1);
        int dot = base.lastIndexOf('.');
        if (dot > 0) base = base.substring(0, dot);
        base = BRACKETS.matcher(base).replaceAll(" ");
        base = base.replace('_', ' ');
        if (!base.trim().contains(" ")) base = base.replace('.', ' ');
        base = NUMBERED.matcher(base).replaceFirst("");
        base = strip(SPACES.matcher(base).replaceAll(" "), " -.");
        Matcher m = ARTICLE.matcher(base);
        if (m.matches()) base = m.group(2) + " " + m.group(1) + (m.group(3) == null ? "" : m.group(3));
        return base;
    }

    private static String words(String s) {
        return " " + s.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]+", " ").trim() + " ";
    }

    /** System suggested by folder names (nearest folder last in the list wins), or null. */
    public static String hintFor(List<String> folderNames) {
        for (int i = folderNames.size() - 1; i >= 0; i--) {
            String w = words(folderNames.get(i));
            for (String[] h : HINTS) {
                for (int k = 1; k < h.length; k++) if (w.contains(" " + h[k] + " ")) return h[0];
            }
        }
        return null;
    }

    private static boolean at(byte[] b, int off, byte[] want) {
        if (b == null || off < 0 || off + want.length > b.length) return false;
        for (int i = 0; i < want.length; i++) if (b[off + i] != want[i]) return false;
        return true;
    }

    private static boolean contains(byte[] b, int from, int to, String s) {
        byte[] w = s.getBytes(StandardCharsets.US_ASCII);
        to = Math.min(to, b.length);
        for (int i = Math.max(0, from); i + w.length <= to; i++) if (at(b, i, w)) return true;
        return false;
    }

    /** Look at the first bytes of an .iso/.bin: a system id or null. */
    public static String sniff(byte[] head) {
        if (head == null) return null;
        if (at(head, 0x1C, new byte[]{(byte) 0xC2, 0x33, (byte) 0x9F, 0x3D}) || at(head, 0x18, new byte[]{0x5D, 0x1C, (byte) 0x9E, (byte) 0xA3})) {
            return "gamecube";
        }
        for (int pvd : new int[]{0x8000, 0x9310, 0x9318}) {
            if (pvd + 6 <= head.length && at(head, pvd + 1, "CD001".getBytes(StandardCharsets.US_ASCII))) {
                if (contains(head, pvd, pvd + 0x100, "PS3VOLUME") || contains(head, 0, head.length, "PS3_GAME")) return "ps3";
                if (contains(head, pvd + 8, pvd + 40, "PLAYSTATION")) return "ps2";
            }
        }
        return null;
    }

    /** Result of classifying one file. system == null with candidates means: ask once for this folder. */
    public static final class Guess {
        public final String system;
        public final List<String> candidates;
        Guess(String s, List<String> c) { system = s; candidates = c; }
    }

    public static Guess classify(String name, List<String> folderNames, byte[] head, Set<String> siblingsLower) {
        String e = ext(name);
        List<String> c = EXT_SYSTEMS.get(e);
        if (c == null) return new Guess(null, Arrays.asList());
        if (e.equals(".bin") && siblingsLower != null) {
            String stem = name.substring(0, name.length() - 4).toLowerCase(Locale.ROOT);
            if (siblingsLower.contains(stem + ".cue")) return new Guess(null, Arrays.asList());
        }
        if (c.size() == 1) return new Guess(c.get(0), c);
        String s = sniff(head);
        if (s != null && c.contains(s)) return new Guess(s, c);
        String h = hintFor(folderNames);
        if (h != null && c.contains(h)) return new Guess(h, c);
        return new Guess(null, c);
    }

    public static int rank(String path) {
        int i = EXT_RANK.indexOf(ext(path));
        return i < 0 ? EXT_RANK.size() : i;
    }

    /** Turns a storage-access document id into a normal file path RetroArch can open, or "" if unknown. */
    public static String pathFromDocId(String docId) {
        if (docId == null) return "";
        if (docId.startsWith("raw:")) return docId.substring(4);
        int colon = docId.indexOf(':');
        if (colon <= 0) return "";
        String vol = docId.substring(0, colon), rest = docId.substring(colon + 1);
        String root = vol.equalsIgnoreCase("primary") ? "/storage/emulated/0" : "/storage/" + vol;
        return rest.isEmpty() ? root : root + "/" + rest;
    }
}
