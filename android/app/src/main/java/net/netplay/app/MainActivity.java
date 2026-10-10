package net.netplay.app;

import android.app.Activity;
import android.app.Dialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import net.netplay.core.Games;
import net.netplay.core.Invite;
import net.netplay.core.Json;
import net.netplay.core.MatchClient;
import net.netplay.core.Plan;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** One screen: see your setup at a glance, pick a game, press the big button, get matched. */
public class MainActivity extends Activity {
    private static final String[] RETROARCH_PACKAGES = {"com.retroarch.aarch64", "com.retroarch", "com.retroarch.ra32"};

    private SharedPreferences prefs;
    private final List<Map<String, Object>> games = new ArrayList<>();
    private int selected = 0;
    private InkHero hero;
    private TextView gameName, gameSystem;
    private InkButton goButton, shareButton;
    private TextView status, linkView;
    private final String[] checkHelp = {"", "", "", ""};
    private MatchClient running;   // non-null while searching
    private Setup setup;
    private String currentLink = "";
    private String currentGameName = "";

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("nm", Context.MODE_PRIVATE);
        loadGames();
        selected = Math.min(prefs.getInt("last_game", 0), Math.max(0, games.size() - 1));
        buildUi();
        refresh();
        setup = new Setup(this, prefs, () -> {
            loadGames();
            selected = Math.max(0, Math.min(selected, games.size() - 1));
            refresh();
        });
        if (!prefs.getBoolean("setup_done", false)) {
            status.setText("Let's set everything up once. It takes about two minutes.");
            hero.post(() -> setup.start());
        } else {
            setup.background(false);
        }
        handleLink(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleLink(intent);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == Setup.PICK_FOLDER && resultCode == RESULT_OK && data != null) setup.onFolderPicked(data.getData());
    }

    /** Someone tapped an invite link (netplay://join?...) in a browser or chat. */
    private void handleLink(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data != null) joinWithLink(data.toString());
    }

    // ------------------------------------------------------------ UI
    private int dp(int v) { return Ink.dp(this, v); }

    private LinearLayout.LayoutParams fill(int top) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(top);
        return lp;
    }

    private LinearLayout.LayoutParams half(int left) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        lp.leftMargin = dp(left);
        return lp;
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Ink.PAPER);
        scroll.addView(root);
        setContentView(scroll);

        hero = new InkHero(this);
        hero.setListener(i -> explainCheck(i));
        root.addView(hero, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(20), dp(8), dp(20), dp(24));
        root.addView(body, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        body.addView(Ink.caption(this, "Game"));
        LinearLayout pick = new LinearLayout(this);
        pick.setOrientation(LinearLayout.HORIZONTAL);
        pick.setGravity(Gravity.CENTER_VERTICAL);
        pick.setFocusable(true);
        pick.setClickable(true);
        pick.setMinimumHeight(dp(60));
        pick.setPadding(dp(14), dp(8), dp(10), dp(8));
        pick.setBackground(Ink.surface(this, false));
        LinearLayout names = new LinearLayout(this);
        names.setOrientation(LinearLayout.VERTICAL);
        gameName = Ink.text(this, "", 16.5f, true, Ink.INK);
        gameSystem = Ink.text(this, "", 13, false, Ink.INK_SOFT);
        names.addView(gameName);
        names.addView(gameSystem);
        pick.addView(names, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        pick.addView(new IconView(this, Icons.CHEVRON, Ink.INK), new LinearLayout.LayoutParams(dp(24), dp(24)));
        pick.setOnClickListener(v -> chooseGame());
        LinearLayout.LayoutParams plp = fill(6);
        body.addView(pick, plp);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        InkButton add = new InkButton(this, Icons.DISC, "Game folders", false);
        add.onPress(() -> setup.step(1, true));
        InkButton remove = new InkButton(this, Icons.PLUS, "Add one", false);
        remove.onPress(this::addGame);
        row.addView(add, half(0));
        row.addView(remove, half(10));
        body.addView(row, fill(10));

        goButton = new InkButton(this, Icons.SEARCH, "Find an opponent", true);
        goButton.setMinimumHeight(dp(68));
        goButton.onPress(() -> { if (running != null) running.cancel(); else search(null); });
        body.addView(goButton, fill(22));

        status = Ink.text(this, "", 15, false, Ink.INK);
        status.setPadding(0, dp(14), 0, dp(4));
        body.addView(status, fill(0));

        linkView = Ink.text(this, "", 12.5f, false, Ink.INK_SOFT);
        linkView.setVisibility(View.GONE);
        linkView.setTextIsSelectable(true);
        body.addView(linkView, fill(4));
        shareButton = new InkButton(this, Icons.SHARE, "Share invite link", false);
        shareButton.setVisibility(View.GONE);
        shareButton.onPress(this::shareLink);
        body.addView(shareButton, fill(8));

        LinearLayout bottom = new LinearLayout(this);
        bottom.setOrientation(LinearLayout.HORIZONTAL);
        InkButton join = new InkButton(this, Icons.LINK, "Join a link", false);
        join.onPress(this::askForLink);
        InkButton settings = new InkButton(this, Icons.GEAR, "Settings", false);
        settings.onPress(this::openSettings);
        bottom.addView(join, half(0));
        bottom.addView(settings, half(10));
        body.addView(bottom, fill(26));
    }

    private void refresh() {
        Map<String, Object> g = currentGame();
        if (g == null) {
            gameName.setText("No game yet");
            gameSystem.setText("Tap Game folders");
        } else {
            gameName.setText((String) g.get("name"));
            gameSystem.setText(Games.SYSTEMS.get((String) g.get("system")).label);
        }
        refreshChecks();
    }

    private Map<String, Object> currentGame() {
        return (selected >= 0 && selected < games.size()) ? games.get(selected) : null;
    }

    private void refreshChecks() {
        Map<String, Object> g = currentGame();
        String[] note = new String[4];
        int[] st = new int[4];
        boolean server = !prefs.getString("server_host", "").isEmpty();
        st[0] = server ? InkHero.READY : InkHero.ATTENTION;
        note[0] = server ? "set" : "not set";
        checkHelp[0] = server ? "The matchmaker address is set. It is where players find each other."
                : "Open Settings and enter the matchmaker address your friend gave you.";
        boolean parsec = prefs.getBoolean("use_parsec", false);
        st[3] = InkHero.READY;
        note[3] = parsec ? "Parsec" : "built in";
        checkHelp[3] = parsec ? "Parsec: you can join another player's stream. A phone can't host one."
                : "Built-in streaming: when you match, the other player's game opens in your browser and your touch controls play it.";
        if (g == null) {
            st[1] = InkHero.ATTENTION; note[1] = "add one";
            checkHelp[1] = "Tap Game folders and pick the folders where your games are. Games are found and named for you, so they match the other player's.";
            st[2] = InkHero.UNKNOWN; note[2] = "-";
            checkHelp[2] = "Pick a game first.";
        } else {
            Games.Sys sys = Games.SYSTEMS.get((String) g.get("system"));
            prefs.edit().putInt("last_game", selected).apply();
            boolean hasRom = !((String) g.get("rom")).isEmpty();
            String again = "\nYour opponent must add this game as \"" + g.get("name") + "\".";
            if (sys.hasNative()) {
                st[1] = hasRom ? InkHero.READY : InkHero.UNKNOWN;
                note[1] = hasRom ? "file set" : "no file";
                checkHelp[1] = (hasRom ? "The game file is set." : "No game file is set. Netplay needs one; streaming doesn't.") + again;
                boolean ra = retroArchPackage() != null;
                st[2] = ra ? InkHero.READY : InkHero.ATTENTION;
                note[2] = ra ? "RetroArch" : "install";
                String pm = prefs.getString("port_message", "");
                checkHelp[2] = (ra ? "RetroArch is installed." : "RetroArch isn't installed. Install it for rollback netplay. Streaming still works.")
                        + (ra && !pm.isEmpty() ? "\n\n" + pm : "");
            } else {
                st[1] = InkHero.READY; note[1] = "stream";
                checkHelp[1] = sys.label + " games play by joining someone's stream." + again;
                st[2] = InkHero.UNKNOWN; note[2] = "not needed";
                checkHelp[2] = "No emulator is needed on this phone for " + sys.label + ".";
            }
        }
        hero.setChecks(new String[]{"Server", "Game", "Emulator", "Stream"}, note, st);
    }

    private void explainCheck(int i) {
        TextView t = Ink.text(this, checkHelp[i], 14.5f, false, Ink.INK);
        String[] titles = {"Server", "Game", "Emulator", "Stream"};
        Ink.sheet(this, titles[i], t, i == 0 ? "Settings" : "OK", i == 0 ? this::openSettings : null, i == 0 ? "Close" : null).show();
    }

    // ------------------------------------------------------------ games
    @SuppressWarnings("unchecked")
    private void loadGames() {
        games.clear();
        try {
            Object o = Json.parse(prefs.getString("games", "[]"));
            for (Object x : (List<Object>) o) {
                if (!(x instanceof Map)) continue;
                Map<String, Object> g = (Map<String, Object>) x;
                // skip anything the screen can't show instead of crashing on it
                if (!(g.get("name") instanceof String) || !(g.get("system") instanceof String)
                        || !Games.SYSTEMS.containsKey(g.get("system"))) continue;
                if (!(g.get("rom") instanceof String)) g.put("rom", "");
                games.add(g);
            }
        } catch (RuntimeException ignored) { }
    }

    private void saveGames() { prefs.edit().putString("games", Json.stringify(games)).apply(); }

    private void chooseGame() {
        if (games.isEmpty()) { setup.step(1, true); return; }
        final LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        final Dialog[] dlg = new Dialog[1];
        for (int i = 0; i < games.size(); i++) {
            final int idx = i;
            Map<String, Object> g = games.get(i);
            TextView row = Ink.text(this, g.get("name") + "\n" + Games.SYSTEMS.get((String) g.get("system")).label, 15, i == selected, Ink.INK);
            row.setContentDescription(g.get("name") + ", " + Games.SYSTEMS.get((String) g.get("system")).label + ". Long press to remove.");
            row.setPadding(dp(14), dp(10), dp(14), dp(10));
            row.setMinimumHeight(dp(56));
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setFocusable(true);
            row.setClickable(true);
            row.setBackground(Ink.surface(this, false));
            row.setOnClickListener(v -> { selected = idx; dlg[0].dismiss(); refresh(); });
            row.setOnLongClickListener(v -> {
                dlg[0].dismiss();
                Ink.sheet(this, "Remove " + games.get(idx).get("name") + "?",
                        Ink.text(this, Boolean.TRUE.equals(games.get(idx).get("auto"))
                                ? "It comes back the next time your game folders are scanned, unless you remove the file or folder."
                                : "Your game file isn't deleted.", 14, false, Ink.INK),
                        "Remove", () -> { selected = idx; removeGame(); }, "Cancel").show();
                return true;
            });
            list.addView(row, fill(idx == 0 ? 0 : 8));
        }
        list.addView(Ink.text(this, "Tip: press and hold a game to remove it.", 12.5f, false, Ink.INK_SOFT), fill(12));
        dlg[0] = Ink.sheet(this, "Choose a game", list, null, null, "Close");
        dlg[0].show();
    }

    private void addGame() {
        final LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        final EditText name = Ink.field(this, "Game name (your opponent types the same)", "");
        box.addView(name);
        box.addView(Ink.caption(this, "System"), fill(14));
        final List<Games.Sys> systems = new ArrayList<>(Games.SYSTEMS.values());
        final int[] pickedSys = {0};
        final List<TextView> chips = new ArrayList<>();
        for (int i = 0; i < systems.size(); i++) {
            final int idx = i;
            TextView chip = Ink.text(this, systems.get(i).label, 14, false, Ink.INK);
            chip.setPadding(dp(14), dp(10), dp(14), dp(10));
            chip.setMinimumHeight(dp(48));
            chip.setGravity(Gravity.CENTER_VERTICAL);
            chip.setFocusable(true);
            chip.setClickable(true);
            chips.add(chip);
            chip.setOnClickListener(v -> {
                pickedSys[0] = idx;
                for (int k = 0; k < chips.size(); k++) {
                    boolean on = k == idx;
                    chips.get(k).setBackground(Ink.surface(this, on));
                    chips.get(k).setTextColor(on ? Ink.PAPER : Ink.INK);
                }
            });
            box.addView(chip, fill(6));
        }
        chips.get(0).performClick();
        final EditText rom = Ink.field(this, "Game file, e.g. /storage/emulated/0/ROMs/game.gba (blank for PS2/PS3)", "");
        box.addView(Ink.caption(this, "Game file"), fill(14));
        box.addView(rom, fill(6));
        Ink.sheet(this, "Add a game", box, "Add", () -> {
            String n = name.getText().toString().trim();
            if (n.isEmpty()) return;
            Map<String, Object> g = new LinkedHashMap<>();
            g.put("name", n);
            g.put("system", systems.get(pickedSys[0]).id);
            g.put("rom", rom.getText().toString().trim());
            games.add(g);
            saveGames();
            selected = games.size() - 1;
            refresh();
        }, "Cancel").show();
    }

    private void removeGame() {
        if (selected >= 0 && selected < games.size()) {
            games.remove(selected);
            saveGames();
            selected = Math.max(0, Math.min(selected, games.size() - 1));
            refresh();
        }
    }

    // ------------------------------------------------------------ settings
    private void openSettings() {
        final LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(Ink.caption(this, "Matchmaker address"));
        final EditText host = Ink.field(this, "for example 203.0.113.5", prefs.getString("server_host", ""));
        box.addView(host, fill(6));
        box.addView(Ink.caption(this, "Port"), fill(12));
        final EditText port = Ink.field(this, "7777", String.valueOf(prefs.getInt("server_port", 7777)));
        box.addView(port, fill(6));
        box.addView(Ink.caption(this, "Public address for invite links"), fill(12));
        final EditText inviteHost = Ink.field(this, "blank = same as above", prefs.getString("invite_host", ""));
        box.addView(inviteHost, fill(6));
        final InkCheck autoPorts = new InkCheck(this, "Open the port on my router automatically (recommended)", prefs.getBoolean("auto_ports", true));
        final InkCheck canHost = new InkCheck(this, "I opened port 55435 on my router myself", prefs.getBoolean("can_host", false));
        final InkCheck inviteOn = new InkCheck(this, "Make an invite link when I search", prefs.getBoolean("invite_on", true));
        final InkCheck randoms = new InkCheck(this, "Also let random players match me", prefs.getBoolean("invite_randoms", true));
        final InkCheck autoShare = new InkCheck(this, "Open the share sheet when I search", prefs.getBoolean("auto_share", true));
        final InkCheck useParsec = new InkCheck(this, "Use Parsec instead of built-in streaming (PS2/PS3)", prefs.getBoolean("use_parsec", false));
        box.addView(autoPorts, fill(14));
        box.addView(canHost, fill(2));
        box.addView(inviteOn, fill(2));
        box.addView(randoms, fill(2));
        box.addView(autoShare, fill(2));
        box.addView(useParsec, fill(2));
        final Dialog[] me = new Dialog[1];
        InkButton again = new InkButton(this, Icons.CHECK, "Run setup again", false);
        again.onPress(() -> { if (me[0] != null) me[0].dismiss(); setup.start(); });
        box.addView(again, fill(16));
        me[0] = Ink.sheet(this, "Settings", box, "Save", () -> {
            int p = 7777;
            try { p = Integer.parseInt(port.getText().toString().trim()); } catch (NumberFormatException ignored) { }
            prefs.edit().putString("server_host", host.getText().toString().trim())
                    .putInt("server_port", p).putBoolean("can_host", canHost.isOn())
                    .putString("invite_host", inviteHost.getText().toString().trim())
                    .putBoolean("invite_on", inviteOn.isOn())
                    .putBoolean("invite_randoms", randoms.isOn())
                    .putBoolean("use_parsec", useParsec.isOn())
                    .putBoolean("auto_share", autoShare.isOn())
                    .putBoolean("auto_ports", autoPorts.isOn()).apply();
            status.setText("Settings saved.");
            refreshChecks();
            setup.background(true);
        }, "Cancel");
        me[0].show();
    }

    // ------------------------------------------------------------ searching
    private Map<String, Object> findGame(String key) {
        for (Map<String, Object> g : games) {
            if (Games.key((String) g.get("system"), (String) g.get("name")).equals(key)) return g;
        }
        return null;
    }

    private static boolean isLocalAddress(String h) {
        String x = h.toLowerCase();
        return x.isEmpty() || x.equals("localhost") || x.startsWith("127.") || x.startsWith("192.168.") || x.startsWith("10.");
    }

    /** join == null: start my own search (and make an invite link). join != null: follow a friend's link. */
    private void search(final Invite join) {
        if (running != null) { status.setText("Finish or cancel the current search first."); return; }
        final Map<String, Object> g;
        final String host, code;
        final int port;
        final boolean only;
        if (join != null) {
            g = findGame(join.game);
            if (g == null) {
                String guess = join.game.substring(join.game.indexOf(':') + 1).replace('-', ' ');
                status.setText("You don't have this game yet. Tap Add and name it \"" + guess + "\" (capital letters don't matter), then open the invite again.");
                return;
            }
            host = join.host; port = join.port; code = join.code; only = true;
        } else {
            g = currentGame();
            if (g == null) { status.setText("Add a game first."); return; }
            host = prefs.getString("server_host", "");
            if (host.isEmpty()) { status.setText("Open Settings and enter the matchmaker address first."); return; }
            port = prefs.getInt("server_port", 7777);
            code = prefs.getBoolean("invite_on", true) ? Invite.newCode() : "";
            only = !prefs.getBoolean("invite_randoms", true);
        }
        final Games.Sys sys = Games.SYSTEMS.get((String) g.get("system"));
        final String rom = (String) g.get("rom");
        final Plan.Offer offer = Plan.offer(sys, retroArchPackage() != null, !rom.isEmpty(), setup.canHostNative());
        final String key = Games.key(sys.id, (String) g.get("name"));
        final MatchClient mc = new MatchClient();
        running = mc;
        goButton.set(Icons.SEARCH, "Cancel search");
        goButton.icon().loop(true);
        hero.setSearching(true);
        status.setText((join != null ? "Joining your friend for " : "Looking for an opponent for ") + g.get("name") + "...");
        if (join == null && !code.isEmpty()) {
            String linkHost = prefs.getString("invite_host", "").trim();
            showInvite((String) g.get("name"), Invite.link(linkHost.isEmpty() ? host : linkHost, port, key, code),
                    isLocalAddress(linkHost.isEmpty() ? host : linkHost));
        }
        new Thread(() -> {
            try {
                Map<String, Object> info = new LinkedHashMap<>();
                info.put("native", new LinkedHashMap<String, Object>());
                info.put("stream", Plan.streamInfo(prefs.getBoolean("use_parsec", false)));
                MatchClient.Match m = mc.search(host, port, key, offer.lanes, info, offer.canHost,
                        "android", code.isEmpty() ? null : code, only);
                runOnUiThread(() -> onMatch(sys, rom, m, host, port));
            } catch (MatchClient.Cancelled c) {
                runOnUiThread(() -> finishSearch("Search cancelled."));
            } catch (Exception e) {
                runOnUiThread(() -> finishSearch("Couldn't reach the matchmaker at " + host + ":" + port
                        + " (" + e.getMessage() + "). Check Settings" + (join != null ? " or ask for a new invite." : ".")));
            }
        }).start();
    }

    // ------------------------------------------------------------ invites
    private void showInvite(String gameName, String link, boolean localOnly) {
        currentLink = link;
        currentGameName = gameName;
        linkView.setText(link);
        linkView.setVisibility(View.VISIBLE);
        shareButton.setVisibility(View.VISIBLE);
        shareButton.icon().play();
        ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        cm.setPrimaryClip(ClipData.newPlainText("netplay invite", link));
        status.setText(status.getText() + " Invite link copied."
                + (localOnly ? " (It only works on your own network until you set a public address in Settings.)" : ""));
        if (prefs.getBoolean("auto_share", true) && !localOnly) shareLink();
    }

    /** The system share sheet reaches every social app you have, with no logins handed to this app. */
    private void shareLink() {
        if (currentLink.isEmpty()) return;
        Intent send = new Intent(Intent.ACTION_SEND);
        send.setType("text/plain");
        send.putExtra(Intent.EXTRA_TEXT, "Looking for a " + currentGameName + " match! Join me: " + currentLink);
        startActivity(Intent.createChooser(send, "Share your invite"));
    }

    private void askForLink() {
        final EditText box = Ink.field(this, "Paste the invite link", "");
        Ink.sheet(this, "Join with a link", box, "Join", () -> joinWithLink(box.getText().toString()), "Cancel").show();
    }

    private void joinWithLink(String text) {
        Invite inv = Invite.parse(text);
        if (inv == null) { status.setText("That doesn't look like a Netplay Matchmaker invite link."); return; }
        search(inv);
    }

    private void finishSearch(String message) {
        running = null;
        goButton.icon().loop(false);
        goButton.set(Icons.SEARCH, "Find an opponent");
        hero.setSearching(false);
        status.setText(message);
        currentLink = "";
        linkView.setVisibility(View.GONE);
        shareButton.setVisibility(View.GONE);
    }

    private void onMatch(Games.Sys sys, String rom, MatchClient.Match m, String host, int port) {
        // Built-in stream: the guest page lives on the matchmaker's web port (TCP port + 1).
        String playUrl = Plan.playUrl(host, port, m.play, m.secureWeb);
        Plan plan = Plan.make(sys, m.lane, m.role, m.peer, playUrl);
        String who = m.peerString("client");
        finishSearch((m.via.equals("invite") ? "Your friend joined from the invite! " : "") + plan.message + (who.isEmpty() ? "" : "  (Opponent is on " + who + ".)"));
        if (plan.kind == Plan.Kind.ERROR) return;
        if (!plan.clipboard.isEmpty()) {
            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            cm.setPrimaryClip(ClipData.newPlainText("netplay", plan.clipboard));
        }
        if (plan.kind == Plan.Kind.OPEN_STREAM) {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(plan.clipboard)));
            } catch (ActivityNotFoundException e) {
                status.setText("No web browser found. Open this address in a browser: " + plan.clipboard + " (copied).");
            }
        } else if (plan.kind == Plan.Kind.OPEN_PARSEC) {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("parsec://peer_id=" + plan.clipboard)));
            } catch (ActivityNotFoundException e) {
                status.setText("Parsec isn't installed. Install the Parsec app, then join peer ID " + plan.clipboard + " (copied).");
            }
        } else {
            launchRetroArch(plan.core, rom);
        }
    }

    // ------------------------------------------------------------ RetroArch
    static boolean retroArchInstalled(android.content.Context c) {
        for (String pkg : RETROARCH_PACKAGES) {
            try { c.getPackageManager().getPackageInfo(pkg, 0); return true; }
            catch (PackageManager.NameNotFoundException ignored) { }
        }
        return false;
    }

    private String retroArchPackage() {
        for (String pkg : RETROARCH_PACKAGES) {
            try {
                getPackageManager().getPackageInfo(pkg, 0);
                return pkg;
            } catch (PackageManager.NameNotFoundException ignored) { }
        }
        return null;
    }

    /** Standard way launchers start RetroArch on Android. */
    private void launchRetroArch(String core, String rom) {
        String pkg = retroArchPackage();
        if (pkg == null) { Toast.makeText(this, "RetroArch isn't installed.", Toast.LENGTH_LONG).show(); return; }
        try {
            Intent i = new Intent(Intent.ACTION_MAIN);
            i.setComponent(new ComponentName(pkg, "com.retroarch.browser.retroactivity.RetroActivityFuture"));
            i.putExtra("ROM", rom);
            i.putExtra("LIBRETRO", "/data/data/" + pkg + "/cores/" + core + "_libretro_android.so");
            i.putExtra("CONFIGFILE", "/storage/emulated/0/Android/data/" + pkg + "/files/retroarch.cfg");
            i.putExtra("QUITFOCUS", "");
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            startActivity(i);
        } catch (RuntimeException e) {
            status.setText("Couldn't start RetroArch: " + e.getMessage());
        }
    }
}
