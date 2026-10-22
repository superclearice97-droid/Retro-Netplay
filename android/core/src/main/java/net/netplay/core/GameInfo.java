package net.netplay.core;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Box art, screenshots, a gameplay clip and details for a game, from gamelist.xml files and media folders that
 * Skraper (or EmulationStation, ES-DE, Batocera, Recalbox) wrote into the player's game folders. Nothing is
 * downloaded. The same rules as desktop/gameinfo.py; tests/test_gameinfo.py checks both agree.
 */
public final class GameInfo {
    private GameInfo() { }

    public static final List<String> IMAGE_EXTS = Arrays.asList(".png", ".jpg", ".jpeg", ".webp");
    public static final List<String> VIDEO_EXTS = Arrays.asList(".mp4", ".mkv", ".avi", ".webm");
    public static final String[] TEXT_TAGS = {"name", "desc", "developer", "publisher", "genre", "players", "rating", "releasedate"};
    private static final String[][] TAG_KINDS = {{"boxart", "box"}, {"thumbnail", "box"}, {"cover", "box"},
            {"screenshot", "screen"}, {"image", "screen"}, {"titleshot", "title"}, {"fanart", "fanart"},
            {"marquee", "logo"}, {"wheel", "logo"}, {"video", "video"}};
    private static final Map<String, String> FOLDER_KINDS = new HashMap<>();
    private static final Map<String, String> SUFFIX_KINDS = new HashMap<>();
    static {
        for (String f : new String[]{"box2dfront", "box2d", "box", "boxes", "boxart", "boxfront", "covers", "cover", "thumbnails", "thumbnail"})
            FOLDER_KINDS.put(f, "box");
        for (String f : new String[]{"screenshots", "screenshot", "ss", "snaps", "snap", "images", "image", "mixrbv1", "mixrbv2", "mix", "downloaded_images"})
            FOLDER_KINDS.put(f, "screen");
        for (String f : new String[]{"titlescreens", "titlescreen", "sstitle", "titles"}) FOLDER_KINDS.put(f, "title");
        for (String f : new String[]{"fanart", "fanarts", "backgrounds"}) FOLDER_KINDS.put(f, "fanart");
        for (String f : new String[]{"marquees", "marquee", "wheel", "wheels", "logos"}) FOLDER_KINDS.put(f, "logo");
        for (String f : new String[]{"videos", "video", "downloaded_videos"}) FOLDER_KINDS.put(f, "video");
        String[][] s = {{"thumb", "box"}, {"boxart", "box"}, {"box", "box"}, {"image", "screen"}, {"screenshot", "screen"},
                {"titleshot", "title"}, {"title", "title"}, {"fanart", "fanart"}, {"marquee", "logo"}, {"wheel", "logo"}, {"video", "video"}};
        for (String[] p : s) SUFFIX_KINDS.put(p[0], p[1]);
    }
    private static final List<String> SUFFIX_FOLDERS = Arrays.asList("images", "videos", "downloaded_images", "downloaded_videos", "media");

    private static final Pattern GAME = Pattern.compile("<game(?:\\s[^>]*)?>(.*?)</game\\s*>", Pattern.DOTALL);
    private static final Pattern TAG = Pattern.compile("<([A-Za-z][A-Za-z0-9_-]*)(?:\\s[^>]*)?>(.*?)</\\1\\s*>", Pattern.DOTALL);
    private static final Pattern CDATA = Pattern.compile("<!\\[CDATA\\[(.*?)\\]\\]>", Pattern.DOTALL);
    private static final Pattern ENT = Pattern.compile("&(#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[a-zA-Z]+);");
    private static final Pattern SUFFIX = Pattern.compile("^(.*)-(thumb|boxart|box|image|screenshot|titleshot|title|fanart|marquee|wheel|video)$");
    private static final Pattern DRIVE = Pattern.compile("^[A-Za-z]:.*", Pattern.DOTALL);
    private static final Pattern YEAR = Pattern.compile("^\\s*(\\d{4}).*", Pattern.DOTALL);

    public static String unescape(String s) {
        Matcher c = CDATA.matcher(s);
        StringBuffer b0 = new StringBuffer();
        while (c.find()) c.appendReplacement(b0, Matcher.quoteReplacement(c.group(1).replace("&", "&amp;")));
        c.appendTail(b0);
        Matcher m = ENT.matcher(b0.toString());
        StringBuffer b = new StringBuffer();
        while (m.find()) {
            String e = m.group(1), r = m.group(0);
            try {
                if (e.startsWith("#x") || e.startsWith("#X")) r = new String(Character.toChars(Integer.parseInt(e.substring(2), 16)));
                else if (e.startsWith("#")) r = new String(Character.toChars(Integer.parseInt(e.substring(1))));
                else if (e.equals("lt")) r = "<";
                else if (e.equals("gt")) r = ">";
                else if (e.equals("amp")) r = "&";
                else if (e.equals("quot")) r = "\"";
                else if (e.equals("apos")) r = "'";
            } catch (IllegalArgumentException ignored) { r = m.group(0); }
            m.appendReplacement(b, Matcher.quoteReplacement(r));
        }
        m.appendTail(b);
        return b.toString();
    }

    /** One spelling for a path, to compare them: forward slashes, no ./ or ../, lower case. */
    public static String norm(String path) {
        String p = path.replace('\\', '/');
        List<String> parts = new ArrayList<>();
        for (String s : p.split("/")) {
            if (s.isEmpty() || s.equals(".")) continue;
            if (s.equals("..")) { if (!parts.isEmpty()) parts.remove(parts.size() - 1); continue; }
            parts.add(s);
        }
        StringBuilder b = new StringBuilder(p.startsWith("/") ? "/" : "");     // (String.join needs Android 8)
        for (int i = 0; i < parts.size(); i++) b.append(i > 0 ? "/" : "").append(parts.get(i));
        return b.toString().toLowerCase(Locale.ROOT);
    }

    public static String stem(String path) {
        String p = path.replace('\\', '/');
        String base = p.substring(p.lastIndexOf('/') + 1);
        int dot = base.lastIndexOf('.');
        return dot > 0 ? base.substring(0, dot) : base;
    }

    private static boolean absolute(String p) { return p.startsWith("/") || DRIVE.matcher(p).matches(); }

    /** gamelist.xml text and the folder it is in -> one map per game ("rom": normalized path, "dir", and its tags). */
    public static List<Map<String, String>> parseGamelist(String xml, String folder) {
        List<Map<String, String>> out = new ArrayList<>();
        Matcher m = GAME.matcher(xml);
        while (m.find() && out.size() < 50000) {
            Map<String, String> g = new LinkedHashMap<>();
            Matcher t = TAG.matcher(m.group(1));
            while (t.find()) {
                String tag = t.group(1).toLowerCase(Locale.ROOT);
                if (!g.containsKey(tag)) g.put(tag, unescape(t.group(2)).trim());
            }
            String path = g.remove("path");
            if (path == null || path.isEmpty()) continue;
            g.put("rom", norm(absolute(path) ? path : folder + "/" + path));
            g.put("dir", folder);
            out.add(g);
        }
        return out;
    }

    /** A media file's path below a system folder -> {kind, rom stem}, or null. */
    public static String[] mediaKind(String rel) {
        List<String> parts = new ArrayList<>();
        for (String s : rel.replace('\\', '/').split("/")) if (!s.isEmpty()) parts.add(s);
        if (parts.size() < 2) return null;
        String name = parts.get(parts.size() - 1);
        String ext = Library.ext(name);
        boolean video = VIDEO_EXTS.contains(ext);
        if (!video && !IMAGE_EXTS.contains(ext)) return null;
        String st = stem(name);
        String folder = parts.get(parts.size() - 2).toLowerCase(Locale.ROOT);
        Matcher m = SUFFIX.matcher(st);
        String kind;
        if (m.matches() && SUFFIX_FOLDERS.contains(folder)) {
            kind = SUFFIX_KINDS.get(m.group(2));
            st = m.group(1);
        } else if (FOLDER_KINDS.containsKey(folder)) {
            kind = FOLDER_KINDS.get(folder);
        } else return null;
        if (kind.equals("video") != video) return null;
        return new String[]{kind, st};
    }

    /** A found media file: where it is (a path or a content URI), its kind and the rom stem it belongs to. */
    public static final class Media {
        public final String where, kind, stem;
        public Media(String where, String kind, String stem) { this.where = where; this.kind = kind; this.stem = stem; }
    }

    /**
     * Everything known for one game. rom: its file path ("" for a streamed game); name: its display name.
     * resolve turns a gamelist's media path (absolute, as written) into something the app can open, or null.
     * -> name, desc, year, developer, publisher, genre, players, rating, box, title, fanart, logo, video (strings)
     * and screens (List of strings); missing parts are empty.
     */
    public static Map<String, Object> collect(String rom, String name, List<Map<String, String>> entries, List<Media> media,
                                              Resolver resolve) {
        Map<String, Object> info = new LinkedHashMap<>();
        for (String k : new String[]{"name", "desc", "year", "developer", "publisher", "genre", "players", "rating", "box"}) info.put(k, "");
        info.put("screens", new ArrayList<String>());
        for (String k : new String[]{"title", "fanart", "logo", "video"}) info.put(k, "");
        String wantRom = rom.isEmpty() ? "" : norm(rom), wantStem = rom.isEmpty() ? "" : stem(rom).toLowerCase(Locale.ROOT);
        String wantClean = Games.slug(rom.isEmpty() ? name : Library.cleanName(rom));
        String romDir = wantRom.contains("/") ? wantRom.substring(0, wantRom.lastIndexOf('/')) : "";

        int best = -1, bestRank = 99;
        for (int i = 0; i < entries.size(); i++) {
            Map<String, String> e = entries.get(i);
            String er = e.get("rom");
            int r;
            String eStem = stem(er).toLowerCase(Locale.ROOT), eDir = er.contains("/") ? er.substring(0, er.lastIndexOf('/')) : "";
            if (!wantRom.isEmpty() && er.equals(wantRom)) r = 0;
            else if (!wantStem.isEmpty() && eStem.equals(wantStem)) r = eDir.equals(romDir) ? 1 : 2;
            else if (!wantClean.isEmpty() && Games.slug(Library.cleanName(er)).equals(wantClean)) r = 3;
            else if (!wantClean.isEmpty() && Games.slug(e.getOrDefault("name", "")).equals(wantClean)) r = 4;
            else continue;
            if (r < bestRank) { bestRank = r; best = i; }
        }
        if (best >= 0) {
            Map<String, String> e = entries.get(best);
            for (String t : TEXT_TAGS) {
                String v = e.getOrDefault(t, "");
                if (t.equals("releasedate")) {
                    Matcher y = YEAR.matcher(v);
                    int year = y.matches() ? Integer.parseInt(y.group(1)) : 0;
                    info.put("year", year >= 1970 && year <= 2100 ? y.group(1) : "");
                } else if (t.equals("rating")) {
                    try {
                        double r = Double.parseDouble(v);
                        if (!Double.isNaN(r)) info.put("rating", String.format(Locale.ROOT, "%.2f", Math.max(0.0, Math.min(1.0, r))));
                    } catch (NumberFormatException ignored) { }
                } else {
                    String w = v.replaceAll("[ \\t]+", " ");
                    info.put(t, w.length() > 4000 ? w.substring(0, 4000) : w);
                }
            }
            for (String[] tk : TAG_KINDS) {
                String v = e.getOrDefault(tk[0], "");
                if (v.isEmpty()) continue;
                String where = resolve.open(absolute(v) ? v : e.get("dir") + "/" + v);
                if (where != null) put(info, tk[1], where);
            }
        }
        List<Object[]> ranked = new ArrayList<>();
        for (Media md : media) {
            String s = md.stem.toLowerCase(Locale.ROOT);
            int r;
            if (!wantStem.isEmpty() && s.equals(wantStem)) r = 0;
            else if (!wantClean.isEmpty() && Games.slug(Library.cleanName(md.stem + ".x")).equals(wantClean)) r = 1;
            else continue;
            ranked.add(new Object[]{r, md});
        }
        ranked.sort((a, b) -> {
            int c = Integer.compare((int) a[0], (int) b[0]);
            return c != 0 ? c : ((Media) a[1]).where.compareTo(((Media) b[1]).where);
        });
        for (Object[] o : ranked) put(info, ((Media) o[1]).kind, ((Media) o[1]).where);
        if (((String) info.get("name")).isEmpty()) info.put("name", name);
        return info;
    }

    /** Turns a media path from a gamelist into something the app can open (a content URI on Android), or null. */
    public interface Resolver { String open(String path); }

    @SuppressWarnings("unchecked")
    private static void put(Map<String, Object> info, String kind, String where) {
        if (kind.equals("screen")) {
            List<String> s = (List<String>) info.get("screens");
            if (!s.contains(where) && s.size() < 8) s.add(where);
        } else if (((String) info.get(kind)).isEmpty()) info.put(kind, where);
    }
}
