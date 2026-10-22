package net.netplay.core;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.CRC32;

/**
 * Optional: a game's box art, screenshots, clip and details from ScreenScraper (screenscraper.fr), the free game
 * database Skraper uses, with the player's own account. The same rules as desktop/screenscraper.py (what is sent,
 * how the answer is read, where it is saved); tests/test_screenscraper.py checks both agree.
 * It sends: the system, the game file's name and size, its CRC32 (files up to 64 MB) or, with no file, the game's
 * name, and the player's ScreenScraper login. Everything is saved in the app's own folder (art/<system>/, laid out
 * like Skraper's output with a gamelist.xml), which GameInfo reads like any other Skraper output.
 */
public final class ScreenScraper {
    private ScreenScraper() { }

    public static final String API = "https://api.screenscraper.fr/api2/";
    public static final String SOFT = "Retro-Netplay";
    public static final long CRC_MAX = 64L * 1024 * 1024;
    public static final int MAX_IMAGE = 8 * 1024 * 1024, MAX_VIDEO = 40 * 1024 * 1024, MAX_ANSWER = 4 * 1024 * 1024;
    static final List<String> REGIONS = Arrays.asList("wor", "us", "eu", "ss", "uk", "fr", "de", "jp");
    /** ScreenScraper media type, kind, media sub-folder, gamelist tag; best first within a kind. */
    static final String[][] MEDIA = {{"box-2D", "box", "box2dfront", "thumbnail"}, {"ss", "screen", "images", "image"},
            {"sstitle", "title", "titlescreens", "titleshot"}, {"wheel-hd", "logo", "wheel", "marquee"},
            {"wheel", "logo", "wheel", "marquee"}, {"fanart", "fanart", "fanart", "fanart"},
            {"video-normalized", "video", "videos", "video"}, {"video", "video", "videos", "video"}};
    public static final String CONSENT = "To find your games' box art, screenshots, gameplay clips and details, Retro-Netplay will send "
            + "ScreenScraper (screenscraper.fr, a free game database run from France) these things for each game you "
            + "choose: its system, its file name and size, a checksum of the file (for files up to 64 MB), and your "
            + "ScreenScraper username and password. Nothing else about you or your phone is sent by the app; "
            + "ScreenScraper sees your internet address, as any website does. ScreenScraper's own rules and privacy "
            + "policy apply (see screenscraper.fr). What you download is saved only on this phone. Game art belongs "
            + "to its owners: keep it for your own game library.";
    private static final Pattern DATE = Pattern.compile("^\\s*([0-9]{4})(?:-([0-9]{2}))?(?:-([0-9]{2}))?.*", Pattern.DOTALL);

    public static int systemId(String system) {
        switch (system == null ? "" : system) {
            case "gba": return 12;
            case "gamecube": return 13;
            case "dreamcast": return 23;
            case "ps1": return 57;
            case "ps2": return 58;
            case "ps3": return 59;
            default: return 0;
        }
    }

    public static final class ScrapeException extends Exception {
        public final String why;
        public ScrapeException(String msg, String why) { super(msg); this.why = why; }
    }

    /** Fetches a URL (at most limit bytes). The default one uses HttpURLConnection; tests pass a pretend one. */
    public interface Http { byte[] get(String url, int limit) throws ScrapeException; }

    /** ScreenScraper's HTTP answers -> plain words. */
    public static ScrapeException why(int code, String body) {
        if (code == 404) return new ScrapeException("ScreenScraper doesn't know this game (yet).", "not_found");
        if (code == 401 || code == 423) return new ScrapeException("ScreenScraper is closed to apps right now (it does this when it's busy). Try again later.", "closed");
        if (code == 403) return new ScrapeException("ScreenScraper turned down this app's developer ID. Update Retro-Netplay, then try again.", "dev");
        if (code == 426) return new ScrapeException("ScreenScraper no longer accepts this version of Retro-Netplay. Update it, then try again.", "outdated");
        if (code == 429) return new ScrapeException("ScreenScraper says too many downloads are running at once. Try again in a minute.", "busy");
        if (code == 430) return new ScrapeException("You've used today's ScreenScraper allowance. It resets tomorrow (a ScreenScraper membership gives more).", "quota");
        if (code == 431) return new ScrapeException("ScreenScraper didn't recognise many of today's files, so it paused. Try again tomorrow.", "unknown_roms");
        String b = body == null ? "" : body.toLowerCase(Locale.ROOT);
        if (b.contains("identifiant") || b.contains("login")) return new ScrapeException("ScreenScraper didn't accept your username or password. Check them in Game art.", "login");
        return new ScrapeException("ScreenScraper answered with an error (" + code + ").", "error");
    }

    public static final Http NET = (url, limit) -> {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) URI.create(url).toURL().openConnection();
            c.setConnectTimeout(30000);
            c.setReadTimeout(30000);
            c.setRequestProperty("User-Agent", SOFT);
            int code = c.getResponseCode();
            if (code >= 400) {
                String body = "";
                try (InputStream e = c.getErrorStream()) { if (e != null) body = new String(read(e, 2000), StandardCharsets.UTF_8); } catch (IOException ignored) { }
                throw why(code, body);
            }
            try (InputStream in = c.getInputStream()) {
                byte[] b = read(in, limit + 1);
                if (b.length > limit) throw new ScrapeException("ScreenScraper sent more than expected; it was left out.", "too_big");
                return b;
            }
        } catch (IOException e) {
            throw new ScrapeException("Couldn't reach ScreenScraper (" + e.getClass().getSimpleName() + "). Check your internet connection.", "network");
        } finally {
            if (c != null) c.disconnect();
        }
    };

    static byte[] read(InputStream in, int limit) throws IOException {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        byte[] buf = new byte[65536];
        int n;
        while (o.size() < limit && (n = in.read(buf, 0, Math.min(buf.length, limit - o.size()))) > 0) o.write(buf, 0, n);
        return o.toByteArray();
    }

    public static String crc32(InputStream in) throws IOException {
        CRC32 c = new CRC32();
        byte[] buf = new byte[1 << 20];
        int n;
        while ((n = in.read(buf)) > 0) c.update(buf, 0, n);
        return String.format(Locale.ROOT, "%08X", c.getValue());
    }

    private static String enc(Object v) {
        try { return URLEncoder.encode(String.valueOf(v), "UTF-8"); } catch (java.io.UnsupportedEncodingException e) { throw new IllegalStateException(e); }
    }

    /**
     * The jeuInfos.php (a file: its name, size and CRC, crc may be "") or jeuRecherche.php (no file: by name) URL.
     * It contains the logins: never log it.
     */
    public static String query(String devId, String devPw, String system, String name, String fileName, long size, String crc,
                               String user, String password) {
        Map<String, Object> p = new LinkedHashMap<>();
        p.put("devid", devId); p.put("devpassword", devPw); p.put("softname", SOFT); p.put("output", "json");
        p.put("ssid", user); p.put("sspassword", password);
        int sys = systemId(system);
        if (sys != 0) p.put("systemeid", sys);
        String page;
        if (fileName != null && !fileName.isEmpty()) {
            p.put("romtype", "rom"); p.put("romnom", fileName); p.put("romtaille", size);
            if (crc != null && !crc.isEmpty()) p.put("crc", crc);
            page = "jeuInfos.php?";
        } else {
            p.put("recherche", name);
            page = "jeuRecherche.php?";
        }
        StringBuilder b = new StringBuilder(API).append(page);
        boolean first = true;
        for (Map.Entry<String, Object> e : p.entrySet()) {
            if (!first) b.append('&');
            first = false;
            b.append(enc(e.getKey())).append('=').append(enc(e.getValue()));
        }
        return b.toString();
    }

    /** Only https from ScreenScraper itself: the request carries the player's login. */
    public static boolean ours(String url) {
        try {
            URI u = new URI(url);
            String host = u.getHost() == null ? "" : u.getHost().toLowerCase(Locale.ROOT);
            return "https".equals(u.getScheme()) && (host.equals("screenscraper.fr") || host.endsWith(".screenscraper.fr"));
        } catch (Exception e) { return false; }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> pick(Object items, String key, List<String> prefer) {
        if (!(items instanceof List) || ((List<Object>) items).isEmpty()) return null;
        List<Object> list = (List<Object>) items;
        for (String want : prefer) for (Object it : list)
            if (it instanceof Map && want.equals(((Map<String, Object>) it).get(key))) return (Map<String, Object>) it;
        return list.get(0) instanceof Map ? (Map<String, Object>) list.get(0) : null;
    }

    @SuppressWarnings("unchecked")
    private static String text(Object v) {
        if (v instanceof Map) { Object t = ((Map<String, Object>) v).get("text"); return t == null ? "" : String.valueOf(t).trim(); }
        return "";
    }

    private static String str(Map<String, Object> m, String k) { Object v = m == null ? null : m.get(k); return v == null ? "" : String.valueOf(v).trim(); }

    /** The details of ScreenScraper's 'jeu': gamelist fields (only those with a value). */
    @SuppressWarnings("unchecked")
    public static Map<String, String> fields(Map<String, Object> jeu) {
        Map<String, String> out = new LinkedHashMap<>();
        Map<String, Object> n = pick(jeu.get("noms"), "region", REGIONS);
        if (n != null) out.put("name", str(n, "text"));
        Map<String, Object> s = pick(jeu.get("synopsis"), "langue", Arrays.asList("en", "fr", "de", "es"));
        if (s != null) out.put("desc", str(s, "text"));
        Map<String, Object> d = pick(jeu.get("dates"), "region", REGIONS);
        if (d != null) {
            Matcher m = DATE.matcher(str(d, "text"));
            if (m.matches()) out.put("releasedate", m.group(1) + (m.group(2) != null ? m.group(2) : "01") + (m.group(3) != null ? m.group(3) : "01") + "T000000");
        }
        out.put("developer", text(jeu.get("developpeur")));
        out.put("publisher", text(jeu.get("editeur")));
        out.put("players", text(jeu.get("joueurs")));
        try {
            double r = Double.parseDouble(text(jeu.get("note")));
            if (!Double.isNaN(r)) out.put("rating", String.format(Locale.ROOT, "%.2f", Math.max(0.0, Math.min(20.0, r)) / 20));
        } catch (NumberFormatException ignored) { }
        Object genres = jeu.get("genres");
        if (genres instanceof List) for (Object g : (List<Object>) genres) {
            Map<String, Object> gn = pick(g instanceof Map ? ((Map<String, Object>) g).get("noms") : null, "langue", Arrays.asList("en", "fr"));
            if (gn != null) { out.put("genre", str(gn, "text")); break; }
        }
        out.values().removeIf(String::isEmpty);
        return out;
    }

    /** The media to fetch for ScreenScraper's 'jeu': {kind, sub-folder, gamelist tag, url, extension}, best first. */
    @SuppressWarnings("unchecked")
    public static List<String[]> media(Map<String, Object> jeu) {
        List<Map<String, Object>> items = new ArrayList<>();
        Object ms = jeu.get("medias");
        if (ms instanceof List) for (Object m : (List<Object>) ms) if (m instanceof Map) items.add((Map<String, Object>) m);
        List<String[]> out = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (String[] t : MEDIA) {
            if (seen.contains(t[1])) continue;
            List<Object> cands = new ArrayList<>();
            for (Map<String, Object> m : items) if (t[0].equals(m.get("type")) && ours(str(m, "url"))) cands.add(m);
            Map<String, Object> best = pick(cands, "region", REGIONS);
            if (best == null) continue;
            Object f = best.get("format");
            String ext = (f == null ? "png" : String.valueOf(f)).toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]", "");
            if (ext.length() > 5) ext = ext.substring(0, 5);
            if (ext.isEmpty()) ext = "png";
            out.add(new String[]{t[1], t[2], t[3], str(best, "url"), ext});
            seen.add(t[1]);
        }
        return out;
    }

    /** Ask ScreenScraper about one game (url from query()) -> its 'jeu'. */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> lookup(String url, Http http) throws ScrapeException {
        Map<String, Object> resp;
        try {
            resp = (Map<String, Object>) Json.parseObject(new String(http.get(url, MAX_ANSWER), StandardCharsets.UTF_8)).get("response");
        } catch (RuntimeException e) {
            throw new ScrapeException("ScreenScraper's answer couldn't be read. Try again later.", "error");
        }
        Object jeu = resp == null ? null : resp.get("jeu");
        if (!(jeu instanceof Map) && resp != null && resp.get("jeux") instanceof List) {
            for (Object j : (List<Object>) resp.get("jeux")) if (j instanceof Map && ((Map<String, Object>) j).get("id") != null) { jeu = j; break; }
        }
        if (!(jeu instanceof Map)) throw new ScrapeException("ScreenScraper doesn't know this game (yet).", "not_found");
        return (Map<String, Object>) jeu;
    }

    private static boolean picture(byte[] d) {
        if (d.length >= 8 && (d[0] & 0xFF) == 0x89 && d[1] == 'P' && d[2] == 'N' && d[3] == 'G') return true;
        if (d.length >= 3 && (d[0] & 0xFF) == 0xFF && (d[1] & 0xFF) == 0xD8 && (d[2] & 0xFF) == 0xFF) return true;
        return d.length >= 12 && d[8] == 'W' && d[9] == 'E' && d[10] == 'B' && d[11] == 'P';
    }

    /**
     * Download one game's details and media into root/<system>/ (gamelist.xml + media/<sub>/). rom: the game's file
     * path as the gamelist should name it ("" when it has none); the url comes from query(). -> the kinds saved.
     */
    public static List<String> scrape(String url, String system, String rom, String name, File root, Http http, boolean videos,
                                      long pauseMs) throws ScrapeException {
        Map<String, Object> jeu = lookup(url, http);
        Map<String, String> fields = fields(jeu);
        String sys = (system == null ? "" : system).replaceAll("[^a-z0-9]", "");
        File sd = new File(root, sys.isEmpty() ? "other" : sys);
        String stem = rom.isEmpty() ? Games.slug(name) : GameInfo.stem(rom);
        if (stem.isEmpty()) stem = "game";
        stem = stem.replaceAll("[\\\\/:*?\"<>|\\x00-\\x1f]", "_");
        if (stem.length() > 150) stem = stem.substring(0, 150);
        List<String> saved = new ArrayList<>();
        Map<String, String> tags = new LinkedHashMap<>();
        for (String[] m : media(jeu)) {
            if (m[0].equals("video") && !videos) continue;
            try { Thread.sleep(pauseMs); } catch (InterruptedException e) { Thread.currentThread().interrupt(); break; }
            byte[] data;
            try {
                data = http.get(m[3], m[0].equals("video") ? MAX_VIDEO : MAX_IMAGE);
            } catch (ScrapeException e) {
                if (Arrays.asList("quota", "busy", "closed", "dev", "outdated", "login", "network").contains(e.why)) throw e;
                continue;
            }
            if (!m[0].equals("video") && !picture(data)) continue;          // not a picture: left out
            File dir = new File(new File(sd, "media"), m[1]);
            File f = new File(dir, stem + "." + m[4]), part = new File(dir, stem + "." + m[4] + ".part");
            try {
                if (!dir.isDirectory() && !dir.mkdirs()) continue;
                try (FileOutputStream o = new FileOutputStream(part)) { o.write(data); }
                if (!part.renameTo(f)) { if (!f.delete() || !part.renameTo(f)) continue; }
            } catch (IOException e) { continue; }
            tags.put(m[2], "./media/" + m[1] + "/" + stem + "." + m[4]);
            saved.add(m[0]);
        }
        Map<String, String> all = new LinkedHashMap<>(fields);
        all.putAll(tags);
        try { writeGamelist(sd, rom.isEmpty() ? "./" + stem + ".game" : rom, all); }
        catch (IOException e) { throw new ScrapeException("The art couldn't be saved on this phone (" + e.getMessage() + ").", "storage"); }
        return saved;
    }

    private static final String[] ORDER = {"name", "desc", "releasedate", "developer", "publisher", "genre", "players", "rating",
            "thumbnail", "image", "titleshot", "marquee", "fanart", "video"};

    /** Add or replace this game in sd/gamelist.xml (Skraper's format, read back by GameInfo). */
    static void writeGamelist(File sd, String rom, Map<String, String> g) throws IOException {
        if (!sd.isDirectory() && !sd.mkdirs()) throw new IOException("no folder");
        File gl = new File(sd, "gamelist.xml");
        String folder = sd.getAbsolutePath().replace('\\', '/');
        List<Map<String, String>> games = gl.isFile()
                ? GameInfo.parseGamelist(new String(readFile(gl), StandardCharsets.UTF_8), folder) : new ArrayList<>();
        String want = GameInfo.norm(rom.startsWith("/") || rom.matches("^[A-Za-z]:.*") ? rom : folder + "/" + rom);
        StringBuilder b = new StringBuilder("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<gameList>\n");
        for (Map<String, String> old : games) if (!old.get("rom").equals(want)) b.append(gameXml(old.get("rom"), old)).append('\n');
        b.append(gameXml(rom, g)).append("\n</gameList>\n");
        File part = new File(sd, "gamelist.xml.part");
        try (FileOutputStream o = new FileOutputStream(part)) { o.write(b.toString().getBytes(StandardCharsets.UTF_8)); }
        if (!part.renameTo(gl)) { if (!gl.delete() || !part.renameTo(gl)) throw new IOException("couldn't replace gamelist.xml"); }
    }

    /** A whole file (at most 20 MB). (java.nio.file needs Android 8; the app runs on 7.) */
    public static byte[] readFile(File f) throws IOException {
        try (InputStream in = new java.io.FileInputStream(f)) { return read(in, 20 * 1024 * 1024); }
    }

    private static String esc(String s) { return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"); }

    private static String gameXml(String path, Map<String, String> g) {
        StringBuilder b = new StringBuilder("  <game><path>").append(esc(path)).append("</path>");
        for (String k : ORDER) { String v = g.get(k); if (v != null && !v.isEmpty()) b.append('<').append(k).append('>').append(esc(v)).append("</").append(k).append('>'); }
        return b.append("</game>").toString();
    }
}
