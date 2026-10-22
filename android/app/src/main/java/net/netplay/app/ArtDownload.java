package net.netplay.app;

import android.app.Activity;
import android.app.Dialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.text.InputType;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import net.netplay.core.Games;
import net.netplay.core.Json;
import net.netplay.core.ScreenScraper;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Game art from ScreenScraper with the player's own account (optional; see net.netplay.core.ScreenScraper for what is
 * sent). Off until the player asks, agrees once to what is sent, and signs in. Needs the app's ScreenScraper developer
 * ID, which the build takes from the project's secrets (BuildConfig); without one it says it isn't available.
 * Downloads go into the app's own folder (files/art/), which the folder scan reads too (Setup.addDownloaded).
 */
final class ArtDownload {
    private ArtDownload() { }

    static boolean available() { return !BuildConfig.SS_DEVID.isEmpty() && !BuildConfig.SS_DEVPASSWORD.isEmpty(); }

    static File root(Context c) { return new File(c.getFilesDir(), "art"); }

    /** games: one game (from its card) or all of them (from Settings); done runs on the main thread afterwards. */
    static void open(Activity a, List<Map<String, Object>> games, Runnable done) {
        SharedPreferences prefs = a.getSharedPreferences("nm", Context.MODE_PRIVATE);
        LinearLayout box = new LinearLayout(a);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(Ink.text(a, "Box art, screenshots, gameplay clips and details come from the files Skraper (or EmulationStation, "
                + "ES-DE, Batocera, Recalbox) saved with your games: copy them along with your games and rescan. "
                + "Retro-Netplay can also download them from ScreenScraper (screenscraper.fr, the database Skraper uses) "
                + "with your own free ScreenScraper account.", 13.5f, false, Ink.INK));
        if (!available()) {
            TextView n = Ink.text(a, "Downloading from ScreenScraper isn't available in this version yet (it needs the app's own "
                    + "ScreenScraper developer ID). Skraper's files work already.", 13.5f, false, Ink.INK_SOFT);
            n.setPadding(0, Ink.dp(a, 10), 0, 0);
            box.addView(n);
            Ink.sheet(a, "Game art", box, null, null, "Close").show();
            return;
        }
        final EditText user = Ink.field(a, "ScreenScraper username", prefs.getString("ss_user", ""));
        final EditText pw = Ink.field(a, "Password", Secret.get(prefs, "ss_password"));
        pw.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        box.addView(user, fill(a, 12));
        box.addView(pw, fill(a, 6));
        box.addView(Ink.caption(a, "No account? Make one for free at screenscraper.fr. The password is kept encrypted on this phone."), fill(a, 4));
        final InkCheck clips = new InkCheck(a, "Download gameplay clips too (a few MB each)", prefs.getBoolean("ss_videos", true));
        box.addView(clips, fill(a, 10));
        final TextView status = Ink.text(a, "", 13.5f, false, Ink.INK);
        box.addView(status, fill(a, 10));
        String label = games.size() == 1 ? "Download for " + games.get(0).get("name") : "Download for all " + games.size() + " games";
        final InkButton go = new InkButton(a, Icons.DOWNLOAD, label.length() > 40 ? label.substring(0, 38) + "..." : label, true);
        box.addView(go, fill(a, 10));
        final Dialog d = Ink.sheet(a, "Game art", box, null, null, "Close");
        final boolean[] running = {false};
        go.onPress(() -> {
            if (running[0]) return;
            String u = user.getText().toString().trim(), p = pw.getText().toString();
            if (u.isEmpty() || p.isEmpty()) { status.setText("Add your ScreenScraper username and password first (an account is free)."); return; }
            prefs.edit().putString("ss_user", u).putBoolean("ss_videos", clips.isOn()).apply();
            Secret.put(prefs, "ss_password", p);
            Runnable start = () -> {
                running[0] = true;
                go.setEnabled(false);
                new Thread(() -> run(a, games, u, p, clips.isOn(), status, () -> {
                    running[0] = false;
                    go.setEnabled(true);
                    if (done != null) done.run();
                })).start();
            };
            if (prefs.getBoolean("ss_consent", false)) { start.run(); return; }
            Ink.sheet(a, "Send game details to ScreenScraper?", Ink.text(a, ScreenScraper.CONSENT, 13.5f, false, Ink.INK),
                    "Agree and download", () -> { prefs.edit().putBoolean("ss_consent", true).apply(); start.run(); }, "Cancel").show();
        });
        d.show();
    }

    private static LinearLayout.LayoutParams fill(Context c, int top) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2);
        lp.topMargin = Ink.dp(c, top);
        return lp;
    }

    private static void say(Activity a, TextView t, String s) { a.runOnUiThread(() -> t.setText(s)); }

    /** Downloads for each game in turn (gently: one at a time, a pause between files), then gives the games their art. */
    private static void run(Activity a, List<Map<String, Object>> games, String user, String pw, boolean videos, TextView status, Runnable done) {
        int got = 0;
        List<String> missing = new ArrayList<>();
        String stop = "";
        Set<String> keys = new HashSet<>();
        for (int i = 0; i < games.size(); i++) {
            Map<String, Object> g = games.get(i);
            String name = String.valueOf(g.get("name")), system = String.valueOf(g.get("system"));
            String rom = g.get("rom") instanceof String ? (String) g.get("rom") : "";
            say(a, status, (i + 1) + " of " + games.size() + ": " + name + "...");
            try {
                String[] file = fileFacts(a, g, rom);          // {name, size, crc}, or nulls when there's no file here
                String url = ScreenScraper.query(BuildConfig.SS_DEVID, BuildConfig.SS_DEVPASSWORD, system, name,
                        file[0], file[1].isEmpty() ? 0 : Long.parseLong(file[1]), file[2], user, pw);
                if (!ScreenScraper.scrape(url, system, file[0].isEmpty() ? "" : rom, name, root(a), ScreenScraper.NET, videos, 1200).isEmpty()) got++;
                keys.add(Games.key(system, name));
            } catch (ScreenScraper.ScrapeException e) {
                if ("not_found".equals(e.why)) { missing.add(name); continue; }
                stop = e.getMessage();
                break;
            }
        }
        if (!keys.isEmpty()) giveArt(a, keys);
        StringBuilder sb = new StringBuilder(got > 0 ? "Art downloaded for " + got + (got == 1 ? " game." : " games.") : "Nothing new was downloaded.");
        if (!missing.isEmpty()) {
            sb.append(" ScreenScraper doesn't know ").append(missing.size()).append(": ");
            for (int k = 0; k < Math.min(5, missing.size()); k++) sb.append(k > 0 ? ", " : "").append(missing.get(k));
            sb.append(missing.size() > 5 ? "..." : ".");
        }
        if (!stop.isEmpty()) sb.append(" Stopped: ").append(stop);
        final String text = sb.toString();
        a.runOnUiThread(() -> { status.setText(text); done.run(); });
    }

    /** The game file's name, size and CRC32 (only up to 64 MB), read through the folder the player picked. */
    private static String[] fileFacts(Activity a, Map<String, Object> g, String rom) {
        String[] out = {"", "", ""};
        if (rom.isEmpty()) return out;
        Object u = g.get("rom_uri");
        try {
            long size = -1;
            Uri uri = u instanceof String ? Uri.parse((String) u) : null;
            if (uri != null) {
                try (Cursor c = a.getContentResolver().query(uri, new String[]{OpenableColumns.SIZE}, null, null, null)) {
                    if (c != null && c.moveToFirst() && !c.isNull(0)) size = c.getLong(0);
                }
            } else if (new File(rom).isFile()) size = new File(rom).length();
            if (size < 0) return out;
            out[0] = rom.substring(rom.replace('\\', '/').lastIndexOf('/') + 1);
            out[1] = String.valueOf(size);
            if (size <= ScreenScraper.CRC_MAX) {
                try (InputStream in = uri != null ? a.getContentResolver().openInputStream(uri) : new FileInputStream(rom)) {
                    if (in != null) out[2] = ScreenScraper.crc32(in);
                }
            }
        } catch (Exception e) { out[0] = ""; out[1] = ""; out[2] = ""; }     // can't read it: look it up by name instead
        return out;
    }

    /** Fill in what each downloaded game is missing (art from the player's own Skraper files comes first). */
    @SuppressWarnings("unchecked")
    static void giveArt(Context c, Set<String> keys) {
        SharedPreferences prefs = c.getSharedPreferences("nm", Context.MODE_PRIVATE);
        Setup.ArtIndex idx = new Setup.ArtIndex();
        Setup.addDownloaded(root(c), idx);
        List<Object> games;
        try { games = (List<Object>) Json.parse(prefs.getString("games", "[]")); } catch (RuntimeException e) { return; }
        for (Object o : games) {
            if (!(o instanceof Map)) continue;
            Map<String, Object> g = (Map<String, Object>) o;
            if (!keys.contains(Hub.key(g))) continue;
            Map<String, Object> mine = g.get("art") instanceof Map ? (Map<String, Object>) g.get("art") : null;
            Map<String, Object> dl = new java.util.LinkedHashMap<>(g);
            dl.remove("art");
            Setup.attachArt(dl, idx);
            if (!(dl.get("art") instanceof Map)) continue;
            Map<String, Object> got = (Map<String, Object>) dl.get("art");
            if (mine == null) { g.put("art", got); continue; }
            for (Map.Entry<String, Object> e : got.entrySet()) {
                Object have = mine.get(e.getKey());
                boolean empty = have == null || (have instanceof String && ((String) have).isEmpty()) || (have instanceof List && ((List<?>) have).isEmpty());
                if (empty) mine.put(e.getKey(), e.getValue());
            }
        }
        prefs.edit().putString("games", Json.stringify(games)).apply();
    }
}
