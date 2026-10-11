package net.netplay.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.database.Cursor;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.provider.DocumentsContract;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import net.netplay.core.Games;
import net.netplay.core.Json;
import net.netplay.core.Library;
import net.netplay.core.PortMap;

import java.io.InputStream;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * First-run setup (matchmaker, game folders, RetroArch, ports) and the background work that keeps it current:
 * rescanning the game folders and keeping the router port open. Nothing here leaves the phone except the
 * router request (UPnP / NAT-PMP, local network only) and the port check sent to the matchmaker you chose.
 */
final class Setup {
    static final int PICK_FOLDER = 41;

    interface Host {
        void setupChanged();
    }

    private final Activity act;
    private final SharedPreferences prefs;
    private final Host host;
    // the open "Your games" sheet, updated when a folder comes back from the picker
    private TextView folderList, folderSummary;
    private LinearLayout questionBox;

    Setup(Activity a, SharedPreferences p, Host h) { act = a; prefs = p; host = h; }

    private int dp(int v) { return Ink.dp(act, v); }

    private LinearLayout.LayoutParams fill(int top) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(top);
        return lp;
    }

    // ================================================================== wizard
    void start() { step(0, false); }

    /** only = true shows just that step (for example "Game folders" from the main screen). */
    void step(int i, boolean only) {
        Runnable next = only ? () -> finish(false) : (i == 3 ? () -> finish(true) : () -> step(i + 1, false));
        Runnable back = only || i == 0 ? null : () -> step(i - 1, false);
        String yes = only || i == 3 ? "Done" : "Next";
        String[] titles = {"Matchmaker", "Your games", "RetroArch", "Playing online"};
        String title = only ? titles[i] : "Step " + (i + 1) + " of 4 \u2014 " + titles[i];
        View content;
        switch (i) {
            case 0: content = serverStep(); break;
            case 1: content = foldersStep(); break;
            case 2: content = retroArchStep(); break;
            default: content = portsStep(); break;
        }
        final Runnable save = pendingSave;
        pendingSave = null;
        Ink.sheet(act, title, content, yes, () -> { if (save != null) save.run(); leaveFolders(); next.run(); },
                back == null ? null : "Back", back == null ? null : () -> { if (save != null) save.run(); leaveFolders(); back.run(); }).show();
    }

    private Runnable pendingSave;

    private void leaveFolders() { folderList = folderSummary = null; questionBox = null; }

    private void finish(boolean wholeSetup) {
        if (wholeSetup) prefs.edit().putBoolean("setup_done", true).apply();
        host.setupChanged();
        background(true);
    }

    private TextView para(String s) {
        TextView t = Ink.text(act, s, 14.5f, false, Ink.INK);
        t.setLineSpacing(0, 1.25f);
        return t;
    }

    private View serverStep() {
        LinearLayout box = new LinearLayout(act);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(Ink.caption(act, "Your name"));
        final EditText name = Ink.field(act, "What friends and opponents see", Hub.get(act).myName());
        box.addView(name, fill(6));
        box.addView(para("Players find each other through a matchmaker. A friend usually runs it and gives you its address."), fill(16));
        box.addView(Ink.caption(act, "Matchmaker address"), fill(14));
        final EditText host = Ink.field(act, "for example 192.168.1.23", prefs.getString("server_host", ""));
        box.addView(host, fill(6));
        box.addView(Ink.caption(act, "Port"), fill(12));
        final EditText port = Ink.field(act, "7777", String.valueOf(prefs.getInt("server_port", 7777)));
        box.addView(port, fill(6));
        final TextView found = Ink.text(act, "", 13.5f, false, Ink.INK);
        InkButton find = new InkButton(act, Icons.SEARCH, "Find on my network", true);
        Runnable look = () -> {
            found.setText("Looking on your home network...");
            Hub.get(act).findOnNetwork(list -> {
                if (list.isEmpty()) {
                    found.setText("No matchmaker found on this Wi-Fi. On the computer, open Retro-Netplay with \"Run the matchmaker "
                            + "on this PC\" on, and make sure both are on the same Wi-Fi. Or type the address a friend gave you.");
                    return;
                }
                net.netplay.core.Lan.Found f = list.get(0);
                host.setText(f.host);
                port.setText(String.valueOf(f.port));
                found.setText("Found " + f + "." + (list.size() > 1 ? " (" + (list.size() - 1) + " more on this network.)" : ""));
            });
        };
        find.onPress(look);
        box.addView(find, fill(12));
        box.addView(found, fill(8));
        if (net.netplay.core.Lan.isLoopback(host.getText().toString())) {
            host.setText("");
            look.run();          // nothing set yet: look straight away
        }
        pendingSave = () -> {
            int p = 7777;
            try { p = Integer.parseInt(port.getText().toString().trim()); } catch (NumberFormatException ignored) { }
            prefs.edit().putString("server_host", host.getText().toString().trim()).putInt("server_port", p).apply();
            Hub.get(act).setName(name.getText().toString());
            Hub.get(act).restartPresence();
        };
        return box;
    }

    private View foldersStep() {
        LinearLayout box = new LinearLayout(act);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(para("Pick the folders where you keep your games. Everything inside, including subfolders, is added and named for you. New games appear the next time you open the app."));
        folderList = Ink.text(act, "", 13, false, Ink.INK_SOFT);
        box.addView(folderList, fill(12));
        InkButton add = new InkButton(act, Icons.PLUS, "Add a folder", false);
        add.onPress(this::pickFolder);
        box.addView(add, fill(10));
        InkButton clear = new InkButton(act, Icons.TRASH, "Forget all folders", false);
        clear.onPress(() -> { prefs.edit().putString("folders", "[]").apply(); rescanAndShow(); });
        box.addView(clear, fill(8));
        folderSummary = Ink.text(act, "", 14, true, Ink.INK);
        box.addView(folderSummary, fill(14));
        questionBox = new LinearLayout(act);
        questionBox.setOrientation(LinearLayout.VERTICAL);
        box.addView(questionBox, fill(6));
        rescanAndShow();
        return box;
    }

    private View retroArchStep() {
        LinearLayout box = new LinearLayout(act);
        box.setOrientation(LinearLayout.VERTICAL);
        boolean ra = MainActivity.retroArchInstalled(act);
        box.addView(para(ra ? "RetroArch is installed. It runs Game Boy Advance, PS1 and Dreamcast games for online play."
                : "RetroArch runs Game Boy Advance, PS1 and Dreamcast games for online play. It's free. PS2, PS3 and GameCube games play by joining a friend's stream, so they don't need it."));
        if (!ra) {
            InkButton get = new InkButton(act, Icons.PLAY, "Get RetroArch", false);
            get.onPress(() -> {
                try {
                    act.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=com.retroarch")));
                } catch (ActivityNotFoundException ignored) { }
            });
            box.addView(get, fill(14));
        }

        // RetroArch network commands: lets this app start hosting and show messages on the game screen
        box.addView(Ink.caption(act, "Let Retro-Netplay talk to RetroArch"), fill(22));
        box.addView(para("In RetroArch open Settings > Network and turn on Network Commands. Then the app can see which game is "
                + "running, start hosting for you, and show \"you're in the queue\" on the game screen. Leave a game running in "
                + "RetroArch and tap Check."), fill(6));
        final TextView raResult = Ink.text(act, prefs.getBoolean("ra_cmds_ok", false) ? "Working." : "", 14, false, Ink.INK);
        InkButton check = new InkButton(act, Icons.SIGNAL, "Check", false);
        check.onPress(() -> {
            raResult.setText("Asking RetroArch...");
            new Thread(() -> {
                boolean ok = net.netplay.core.RetroCmd.available();
                prefs.edit().putBoolean("ra_cmds_ok", ok).apply();
                if (ok) net.netplay.core.RetroCmd.showMsg("Retro-Netplay can talk to RetroArch.");
                act.runOnUiThread(() -> raResult.setText(ok ? "Working. You'll see a message in RetroArch."
                        : "No answer. Is RetroArch open, and is Network Commands on? (Settings > Network)"));
            }).start();
        });
        box.addView(check, fill(10));
        box.addView(raResult, fill(8));

        // the controller shortcut
        box.addView(Ink.caption(act, "Controller shortcut"), fill(22));
        box.addView(para("Hold Start + Select for 3 seconds in any game to join the queue (and again to leave). Android only lets an app "
                + "see these buttons through an accessibility switch. Retro-Netplay looks at Start and Select only, never stores or sends "
                + "your button presses, never reads your screen, and always passes every press on to the game."), fill(6));
        final TextView hkResult = Ink.text(act, HotkeyService.isOn(act) ? "The shortcut is on." : "The shortcut is off.", 14, false, Ink.INK);
        InkButton turnOn = new InkButton(act, Icons.PAD, "Turn the shortcut on or off", false);
        turnOn.onPress(() -> {
            try {
                act.startActivity(new Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS));
                hkResult.setText("In the list, open Retro-Netplay controller shortcut, switch it on, then come back.");
            } catch (ActivityNotFoundException ignored) { hkResult.setText("Open your phone's Accessibility settings and switch on Retro-Netplay controller shortcut."); }
        });
        box.addView(turnOn, fill(10));
        InkButton test = new InkButton(act, Icons.CHECK, "Test it", false);
        test.onPress(() -> {
            if (!HotkeyService.isOn(act)) { hkResult.setText("Switch the shortcut on first."); return; }
            Hub.get(act).onHotkeyTest = () -> hkResult.setText("It works! Holding Start + Select in a game will now queue you.");
            Hub.get(act).hotkeyTest = true;
            hkResult.setText("Now hold Start + Select for 3 seconds...");
        });
        box.addView(test, fill(8));
        box.addView(hkResult, fill(8));
        box.addView(para("Tip: if RetroArch opens its menu with Start + Select, change RetroArch's Menu Toggle combo "
                + "(Settings > Input > Hotkeys) so the two don't clash."), fill(10));
        return box;
    }

    private View portsStep() {
        LinearLayout box = new LinearLayout(act);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(para("Hosting a game needs your router to let the other player in. The app can ask your router to open just the port RetroArch uses. Joining other people works either way."));
        final InkCheck auto = new InkCheck(act, "Open the port on my router automatically (recommended)", prefs.getBoolean("auto_ports", true));
        box.addView(auto, fill(14));
        final TextView result = Ink.text(act, lastPortMessage(), 14, false, Ink.INK);
        InkButton test = new InkButton(act, Icons.SIGNAL, "Test my connection", false);
        test.onPress(() -> {
            prefs.edit().putBoolean("auto_ports", auto.isOn()).apply();
            result.setText("Asking your router and the matchmaker...");
            new Thread(() -> {
                String msg;
                try { msg = refreshPorts(); } catch (Throwable t) { msg = "Couldn't check the connection. Try again in a moment."; }
                final String shown = msg;
                act.runOnUiThread(() -> result.setText(shown));
            }).start();
        });
        box.addView(test, fill(12));
        box.addView(result, fill(12));
        pendingSave = () -> prefs.edit().putBoolean("auto_ports", auto.isOn()).apply();
        return box;
    }

    // ================================================================== folders
    private void pickFolder() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        try { act.startActivityForResult(i, PICK_FOLDER); }
        catch (ActivityNotFoundException e) { if (folderSummary != null) folderSummary.setText("This phone has no folder picker."); }
    }

    /** Called by MainActivity.onActivityResult. */
    void onFolderPicked(Uri tree) {
        if (tree == null) return;
        try { act.getContentResolver().takePersistableUriPermission(tree, Intent.FLAG_GRANT_READ_URI_PERMISSION); }
        catch (SecurityException ignored) { }
        List<String> f = folders();
        if (!f.contains(tree.toString())) f.add(tree.toString());
        prefs.edit().putString("folders", Json.stringify(f)).apply();
        rescanAndShow();
    }

    @SuppressWarnings("unchecked")
    List<String> folders() {
        List<String> out = new ArrayList<>();
        try { for (Object o : (List<Object>) Json.parse(prefs.getString("folders", "[]"))) out.add((String) o); }
        catch (RuntimeException ignored) { }
        return out;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> answers() {
        try { return (Map<String, Object>) Json.parse(prefs.getString("folder_answers", "{}")); }
        catch (RuntimeException e) { return new LinkedHashMap<>(); }
    }

    private static String label(String treeUri) {
        try {
            String id = DocumentsContract.getTreeDocumentId(Uri.parse(treeUri));
            String path = Library.pathFromDocId(id);
            return path.isEmpty() ? id : path.replace("/storage/emulated/0", "Phone");
        } catch (Throwable e) { return treeUri; }
    }

    private void rescanAndShow() {
        if (folderSummary == null) return;
        List<String> f = folders();
        StringBuilder sb = new StringBuilder();
        for (String s : f) sb.append(label(s)).append('\n');
        folderList.setText(f.isEmpty() ? "No folders yet." : sb.toString().trim());
        folderSummary.setText(f.isEmpty() ? "" : "Looking for games...");
        new Thread(() -> {
            final List<Question> qs = new ArrayList<>();
            final List<Map<String, Object>> found = scan(qs);
            act.runOnUiThread(() -> {
                try {
                    saveScan(found);
                    showScan(found, qs);
                    host.setupChanged();
                } catch (Throwable t) {
                    if (folderSummary != null) folderSummary.setText("Something went wrong reading these folders. Try another folder.");
                }
            });
        }).start();
    }

    private void showScan(List<Map<String, Object>> found, List<Question> qs) {
        if (folderSummary == null) return;
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (Map<String, Object> g : found) counts.merge((String) g.get("system"), 1, Integer::sum);
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, Integer> e : counts.entrySet()) {
            if (sb.length() > 0) sb.append(", ");
            sb.append(e.getValue()).append(' ').append(Games.SYSTEMS.get(e.getKey()).label);
        }
        folderSummary.setText(found.isEmpty() ? (folders().isEmpty() ? "" : "No games found in these folders yet.")
                : "Found " + found.size() + " game" + (found.size() == 1 ? "" : "s") + ": " + sb);
        questionBox.removeAllViews();
        if (qs.isEmpty()) return;
        questionBox.addView(para("Some files could be for more than one system. Tell me once for each folder:"));
        for (final Question q : qs) {
            questionBox.addView(Ink.text(act, q.count + " " + q.ext + " file" + (q.count == 1 ? "" : "s") + " in " + q.folder, 13.5f, true, Ink.INK), fill(10));
            LinearLayout row = new LinearLayout(act);
            row.setOrientation(LinearLayout.VERTICAL);
            List<String> opts = new ArrayList<>(q.candidates);
            opts.add("skip");
            for (final String c : opts) {
                TextView chip = Ink.text(act, c.equals("skip") ? "Skip these" : Games.SYSTEMS.get(c).label, 14, false, Ink.INK);
                chip.setPadding(dp(14), dp(10), dp(14), dp(10));
                chip.setMinimumHeight(dp(48));
                chip.setGravity(Gravity.CENTER_VERTICAL);
                chip.setFocusable(true);
                chip.setClickable(true);
                chip.setBackground(Ink.surface(act, false));
                chip.setOnClickListener(v -> {
                    Map<String, Object> a = answers();
                    a.put(q.key, c);
                    prefs.edit().putString("folder_answers", Json.stringify(a)).apply();
                    rescanAndShow();
                });
                row.addView(chip, fill(6));
            }
            questionBox.addView(row);
        }
    }

    static final class Question {
        String key, folder, ext;
        List<String> candidates;
        int count;
    }

    /** Walk every chosen folder through the storage-access framework. Runs off the main thread. */
    List<Map<String, Object>> scan(List<Question> questionsOut) {
        Map<String, Map<String, Object>> found = new LinkedHashMap<>();
        Map<String, Question> qs = new LinkedHashMap<>();
        Map<String, Object> ans = answers();
        int[] budget = {20000};
        for (String s : folders()) {
            try {
                Uri tree = Uri.parse(s);
                walk(tree, DocumentsContract.getTreeDocumentId(tree), 0, found, qs, ans, budget);
            } catch (Throwable ignored) { } // one bad folder or file never stops the app
        }
        questionsOut.addAll(qs.values());
        return new ArrayList<>(found.values());
    }

    private void walk(Uri tree, String docId, int depth, Map<String, Map<String, Object>> found, Map<String, Question> qs,
                      Map<String, Object> ans, int[] budget) {
        if (depth > 6 || budget[0] <= 0) return;
        Uri kids = DocumentsContract.buildChildDocumentsUriUsingTree(tree, docId);
        List<String[]> entries = new ArrayList<>();
        try (Cursor c = act.getContentResolver().query(kids, new String[]{DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME, DocumentsContract.Document.COLUMN_MIME_TYPE}, null, null, null)) {
            if (c == null) return;
            while (c.moveToNext() && budget[0]-- > 0) entries.add(new String[]{c.getString(0), c.getString(1), c.getString(2)});
        } catch (RuntimeException e) { return; }
        Set<String> siblings = new HashSet<>();
        for (String[] e : entries) if (e[1] != null) siblings.add(e[1].toLowerCase(Locale.ROOT));
        String rel = docId.contains(":") ? docId.substring(docId.indexOf(':') + 1) : docId;
        List<String> folderNames = new ArrayList<>(Arrays.asList(rel.split("/")));
        for (String[] e : entries) {
            String id = e[0], name = e[1], mime = e[2];
            if (name == null || name.startsWith(".")) continue;
            if (DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)) {
                walk(tree, id, depth + 1, found, qs, ans, budget);
                continue;
            }
            String ext = Library.ext(name);
            if (!Library.EXT_SYSTEMS.containsKey(ext)) continue;
            byte[] head = (ext.equals(".iso") || ext.equals(".bin")) ? readHead(DocumentsContract.buildDocumentUriUsingTree(tree, id)) : null;
            Library.Guess g = Library.classify(name, folderNames, head, siblings);
            String system = g.system;
            if (system == null && !g.candidates.isEmpty()) {
                String qkey = docId + "|" + ext;
                Object a = ans.get(qkey);
                if ("skip".equals(a)) continue;
                if (a instanceof String && g.candidates.contains(a)) system = (String) a;
                else {
                    Question q = qs.get(qkey);
                    if (q == null) {
                        q = new Question();
                        q.key = qkey; q.ext = ext; q.candidates = g.candidates;
                        q.folder = folderNames.isEmpty() ? "this folder" : folderNames.get(folderNames.size() - 1);
                        qs.put(qkey, q);
                    }
                    q.count++;
                    continue;
                }
            }
            if (system == null) continue;
            String clean = Library.cleanName(name);
            if (clean.isEmpty()) continue;
            String path = Library.pathFromDocId(id);
            String key = Games.key(system, clean);
            Map<String, Object> old = found.get(key);
            if (old != null && Library.rank((String) old.get("rom")) <= Library.rank(path)) continue;
            Map<String, Object> game = new LinkedHashMap<>();
            game.put("name", clean);
            game.put("system", system);
            game.put("rom", path);
            game.put("auto", true);
            found.put(key, game);
        }
    }

    private byte[] readHead(Uri doc) {
        try (InputStream in = act.getContentResolver().openInputStream(doc)) {
            if (in == null) return null;
            byte[] b = new byte[Library.SNIFF_BYTES];
            int off = 0, n;
            while (off < b.length && (n = in.read(b, off, b.length - off)) > 0) off += n;
            return off == b.length ? b : Arrays.copyOf(b, off);
        } catch (Exception e) { return null; }
    }

    /** Replace the automatic games with this scan; games added by hand are kept. */
    @SuppressWarnings("unchecked")
    void saveScan(List<Map<String, Object>> found) {
        List<Map<String, Object>> games = new ArrayList<>();
        try { for (Object o : (List<Object>) Json.parse(prefs.getString("games", "[]"))) games.add((Map<String, Object>) o); }
        catch (RuntimeException ignored) { }
        List<Map<String, Object>> out = new ArrayList<>();
        Set<String> keys = new HashSet<>();
        for (Map<String, Object> g : games) {
            if (Boolean.TRUE.equals(g.get("auto"))) continue;
            out.add(g);
            keys.add(Games.key((String) g.get("system"), (String) g.get("name")));
        }
        for (Map<String, Object> g : found) {
            if (!keys.contains(Games.key((String) g.get("system"), (String) g.get("name")))) out.add(g);
        }
        prefs.edit().putString("games", Json.stringify(out)).apply();
    }

    // ================================================================== ports
    private String gatewayIp() {
        try {
            WifiManager w = (WifiManager) act.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            @SuppressWarnings("deprecation") int g = w.getDhcpInfo().gateway;
            if (g == 0) return "";
            return (g & 0xFF) + "." + ((g >> 8) & 0xFF) + "." + ((g >> 16) & 0xFF) + "." + ((g >> 24) & 0xFF);
        } catch (RuntimeException e) { return ""; }
    }

    String lastPortMessage() { return prefs.getString("port_message", ""); }

    /** Open the RetroArch port and check it. Saves "port_state": open, closed, mapped, failed or off. Slow: off the main thread. */
    String refreshPorts() {
        if (!prefs.getBoolean("auto_ports", true)) {
            prefs.edit().putString("port_state", "off").putString("port_message", "").apply();
            return "Automatic port opening is off.";
        }
        int port = PortMap.RETROARCH_PORT;
        PortMap.Result r = PortMap.open(port, "TCP", gatewayIp(), null);
        String state = r.ok ? "mapped" : "failed", msg = r.message;
        String host = prefs.getString("server_host", "");
        if (!host.isEmpty() && !isLocal(host)) {
            PortMap.Check c = PortMap.check(host, prefs.getInt("server_port", 7777), port);
            if (c.open != null) { state = c.open ? "open" : "closed"; msg = c.message; }
        }
        prefs.edit().putString("port_state", state).putString("port_message", msg).putLong("port_time", System.currentTimeMillis()).apply();
        return msg;
    }

    static boolean isLocal(String h) {
        String x = h.toLowerCase(Locale.ROOT);
        return x.equals("localhost") || x.startsWith("127.") || x.startsWith("192.168.") || x.startsWith("10.")
                || x.matches("^172\\.(1[6-9]|2\\d|3[01])\\..*");
    }

    /** Can this phone host native netplay? A tick in Settings, or the router port proven or opened. */
    boolean canHostNative() {
        String s = prefs.getString("port_state", "");
        return prefs.getBoolean("can_host", false) || s.equals("open") || s.equals("mapped");
    }

    /** Rescan folders and refresh the router port, quietly. force: ignore the 30-minute pause. */
    void background(boolean force) {
        final boolean ports = force || System.currentTimeMillis() - prefs.getLong("port_time", 0) > 30 * 60 * 1000L;
        new Thread(() -> {
            try {
                if (!folders().isEmpty()) {
                    List<Map<String, Object>> found = scan(new ArrayList<>());
                    act.runOnUiThread(() -> { try { saveScan(found); host.setupChanged(); } catch (Throwable ignored) { } });
                }
                if (ports && MainActivity.retroArchInstalled(act)) {
                    refreshPorts();
                    act.runOnUiThread(() -> { try { host.setupChanged(); } catch (Throwable ignored) { } });
                }
            } catch (Throwable ignored) { } // background work is best effort; it must never close the app
        }).start();
    }
}
