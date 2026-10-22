package net.netplay.app;

import android.Manifest;
import android.app.Activity;
import android.app.Dialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.res.Configuration;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import net.netplay.core.Friends;
import net.netplay.core.Games;
import net.netplay.core.Invite;
import net.netplay.core.Json;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Home: your games. Tap one to start it and join the queue. The card at the top says where you are
 * (in the queue, matched, playing). Friends, links and settings sit underneath.
 */
public class MainActivity extends Activity implements Hub.Ui {
    private SharedPreferences prefs;
    private Hub hub;
    private Setup setup;
    private InkHero hero;
    private TextView stateTitle, stateText, status, gamesCaption;
    private InkButton leaveButton, friendsButton, streamButton;
    private LinearLayout gameList;
    private EditText filter;
    private final String[] checkHelp = {"", "", "", ""};
    private int buttonMode = -1;
    private String listSignature = "";
    private final android.os.Handler ticker = new android.os.Handler(android.os.Looper.getMainLooper());
    private final Runnable tick = new Runnable() {
        @Override public void run() { showState(); ticker.postDelayed(this, 1000); }
    };

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("nm", Context.MODE_PRIVATE);
        hub = Hub.get(this);
        buildUi();
        setup = new Setup(this, prefs, this::hubChanged);
        if (!prefs.getBoolean("setup_done", false)) {
            status.setText("Let's set everything up once. It takes about two minutes.");
            hero.post(() -> setup.start());
        } else {
            setup.background(false);
        }
        askForNotifications();
        handleLink(getIntent());
        if ("address".equals(getIntent().getStringExtra("open"))) hero.post(this::openAddress);
    }

    @Override protected void onStart() {
        super.onStart();
        hub.addUi(this);
        if (!hub.hasPresence()) hub.restartPresence();
        if (prefs.getBoolean("setup_done", false)) hub.autoFind();
        hubChanged();
        ticker.post(tick);
    }

    @Override protected void onStop() {
        hub.removeUi(this);     // the hub goes offline by itself once no screen is open and you're not playing
        ticker.removeCallbacks(tick);
        super.onStop();
    }

    /** Turning a handheld or tablet: switch between the side-by-side and stacked layouts, keeping everything else. */
    @Override public void onConfigurationChanged(Configuration c) {
        super.onConfigurationChanged(c);
        String keep = filter == null ? "" : filter.getText().toString();
        buildUi();
        filter.setText(keep);
        listSignature = "";
        buttonMode = -1;
        hubChanged();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleLink(intent);
        if ("address".equals(intent.getStringExtra("open"))) openAddress();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == Setup.PICK_FOLDER && resultCode == RESULT_OK && data != null) setup.onFolderPicked(data.getData());
    }

    private void askForNotifications() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                && !prefs.getBoolean("asked_notif", false)) {
            prefs.edit().putBoolean("asked_notif", true).apply();
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 5);
        }
    }

    /** netplay://join (invite) and netplay://friend links from a browser or chat app. */
    private void handleLink(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data == null) return;
        String s = data.toString();
        Friends.Friend f = Friends.parse(s);
        if (f != null) {
            // a friend link names its matchmaker: use it if you don't have one yet (friends must share one)
            String h = data.getQueryParameter("host");
            if (h == null && ("http".equals(data.getScheme()) || "https".equals(data.getScheme()))) h = data.getHost();
            String pt = data.getQueryParameter("port");
            int port = 7777;
            try { port = pt != null ? Integer.parseInt(pt) : (data.getPort() > 0 ? data.getPort() - 1 : 7777); } catch (NumberFormatException ignored) { }
            if (hub.serverHost().isEmpty() && h != null && !net.netplay.core.Lan.isLoopback(h) && h.matches("^[A-Za-z0-9.\\-\\[\\]:]{1,255}$")) {
                hub.useMatchmaker(h, port);
                status.setText("Connecting to your friend's matchmaker...");
                final Friends.Friend ff = f;
                hero.postDelayed(() -> addFriendFromLink(ff), 2500);
                return;
            }
            addFriendFromLink(f);
            return;
        }
        joinWithLink(s);
    }

    static boolean retroArchInstalled(Context c) { return Hub.get(c).retroArchPackage() != null; }

    // ================================================================ layout
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

    private boolean wide() {
        Configuration c = getResources().getConfiguration();
        return c.orientation == Configuration.ORIENTATION_LANDSCAPE || c.screenWidthDp >= 720;
    }

    private void buildUi() {
        hero = new InkHero(this);
        hero.setListener(this::explainCheck);

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(20), dp(12), dp(20), dp(24));
        buildBody(body);

        if (wide()) {
            // side by side: the picture on the left, everything you press on the right (handhelds, tablets)
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setBackgroundColor(Ink.PAPER);
            FrameLayout left = new FrameLayout(this);
            left.addView(hero, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
            row.addView(left, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 0.42f));
            ScrollView sv = new ScrollView(this);
            sv.addView(body);
            row.addView(sv, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 0.58f));
            setContentView(row);
        } else {
            LinearLayout col = new LinearLayout(this);
            col.setOrientation(LinearLayout.VERTICAL);
            col.addView(hero, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            col.addView(body, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            ScrollView sv = new ScrollView(this);
            sv.setFillViewport(true);
            sv.setBackgroundColor(Ink.PAPER);
            sv.addView(col);
            setContentView(sv);
        }
    }

    private void buildBody(LinearLayout body) {
        // where you are: idle / in the queue / playing
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(14), dp(16), dp(14));
        card.setBackground(Ink.box(Ink.PAPER_DEEP, Ink.INK, 1, this));
        stateTitle = Ink.text(this, "", 17, true, Ink.INK);
        stateText = Ink.text(this, "", 13.5f, false, Ink.INK_SOFT);
        card.addView(stateTitle);
        card.addView(stateText, fill(4));
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        leaveButton = new InkButton(this, Icons.CLOSE, "Leave queue", true);
        leaveButton.onPress(() -> hub.leave());
        streamButton = new InkButton(this, Icons.PLAY, "Open stream", false);
        streamButton.onPress(() -> {
            String u = hub.lastStreamUrl;
            if (u.startsWith("player:")) PlayerActivity.open(this, u);
            else if (!u.isEmpty()) hub.openUrl(u);
        });
        actions.addView(leaveButton, half(0));
        actions.addView(streamButton, half(10));
        card.addView(actions, fill(12));
        body.addView(card, fill(0));

        status = Ink.text(this, "", 14, false, Ink.INK);
        status.setPadding(0, dp(10), 0, 0);
        body.addView(status, fill(0));

        // your games
        gamesCaption = Ink.caption(this, "Your games");
        body.addView(gamesCaption, fill(18));
        body.addView(Ink.text(this, "Tap a game to start it and join the queue. Hold Start + Select for 3 seconds in a game to join or leave the queue.",
                12.5f, false, Ink.INK_SOFT), fill(4));
        filter = Ink.field(this, "Find a game", "");
        filter.setVisibility(View.GONE);
        filter.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence s, int a, int b, int c) { }
            public void onTextChanged(CharSequence s, int a, int b, int c) { }
            public void afterTextChanged(Editable s) { fillGames(); }
        });
        body.addView(filter, fill(10));
        gameList = new LinearLayout(this);
        gameList.setOrientation(LinearLayout.VERTICAL);
        body.addView(gameList, fill(8));

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        InkButton folders = new InkButton(this, Icons.DISC, "Game folders", false);
        folders.onPress(() -> setup.step(1, true));
        InkButton add = new InkButton(this, Icons.PLUS, "Add one", false);
        add.onPress(this::addGame);
        row.addView(folders, half(0));
        row.addView(add, half(10));
        body.addView(row, fill(12));

        // people
        body.addView(Ink.caption(this, "Friends"), fill(22));
        friendsButton = new InkButton(this, Icons.SIGNAL, "Friends", true);
        friendsButton.setMinimumHeight(dp(60));
        friendsButton.onPress(() -> FriendsActivity.open(this));
        body.addView(friendsButton, fill(8));

        LinearLayout bottom = new LinearLayout(this);
        bottom.setOrientation(LinearLayout.HORIZONTAL);
        InkButton join = new InkButton(this, Icons.LINK, "Join a link", false);
        join.onPress(this::askForLink);
        InkButton settings = new InkButton(this, Icons.GEAR, "Settings", false);
        settings.onPress(this::openSettings);
        bottom.addView(join, half(0));
        bottom.addView(settings, half(10));
        body.addView(bottom, fill(10));
    }

    // ================================================================ state
    @Override public void hubChanged() {
        showState();
        fillGames();
        refreshChecks();
        int on;
        synchronized (hub.online) { on = hub.online.size(); }
        int n = hub.friends().size();
        int rq = hub.requests().size();
        friendsButton.set(Icons.SIGNAL, (n == 0 ? "Friends" : "Friends  ·  " + on + " of " + n + " online")
                + (rq == 0 ? "" : "  ·  " + rq + " request" + (rq == 1 ? "" : "s")));
    }

    @Override public void say(String message) { status.setText(message); }

    @Override public void incomingCall(Hub.Call c) {
        Ink.sheet(this, c.name, Ink.text(this, c.name + " " + c.what(hub) + ".", 15, false, Ink.INK),
                "Allow", () -> hub.answer(c.id, true), "Deny", () -> hub.answer(c.id, false)).show();
    }

    private void showState() {
        if (stateTitle == null) return;
        boolean q = hub.state == Hub.State.QUEUE;
        hero.setSearching(q);
        if (q) {
            long s = (System.currentTimeMillis() - hub.queueSince) / 1000;
            stateTitle.setText("In the queue · " + String.format(Locale.ROOT, "%d:%02d", s / 60, s % 60));
            stateText.setText(hub.queueWith.isEmpty() ? hub.queueName + (hub.friends().isEmpty() ? "" : " · friends first") : "Connecting you with " + hub.queueWith + " · " + hub.queueName);
        } else if (!hub.playingKey.isEmpty()) {
            stateTitle.setText("Playing " + hub.gameTitle(hub.playingKey));
            stateText.setText(hub.playingWith.isEmpty() ? "Hold Start + Select for 3 seconds to look for an opponent." : "With " + hub.playingWith);
        } else {
            stateTitle.setText("Not in the queue");
            stateText.setText(hub.serverHost().isEmpty() ? "Looking for Retro-Netplay on your Wi-Fi. Open it on your computer too." : "Pick a game below.");
        }
        int mode = q ? 1 : (!hub.playingKey.isEmpty() ? 2 : 0);
        if (mode != buttonMode) {          // only when it changes, so the icon doesn't redraw every second
            buttonMode = mode;
            leaveButton.setVisibility(mode == 0 ? View.GONE : View.VISIBLE);
            if (mode == 2) {
                leaveButton.set(Icons.CHECK, "Done playing");
                leaveButton.onPress(() -> hub.doneSession());
            } else {
                leaveButton.set(Icons.CLOSE, "Leave queue");
                leaveButton.onPress(() -> hub.leave());
            }
        }
        streamButton.setVisibility(!hub.lastStreamUrl.isEmpty() && !q ? View.VISIBLE : View.GONE);
        if (!hub.message.isEmpty() && status.getText().length() == 0) status.setText(hub.message);
    }

    private void fillGames() {
        if (gameList == null) return;
        List<Map<String, Object>> games = hub.games();
        gamesCaption.setText(("Your games" + (games.isEmpty() ? "" : "  ·  " + games.size())).toUpperCase());
        filter.setVisibility(games.size() > 10 ? View.VISIBLE : View.GONE);
        String f = filter.getText().toString().trim().toLowerCase(Locale.ROOT);
        String playing0 = hub.state == Hub.State.QUEUE ? hub.queueKey : hub.playingKey;
        StringBuilder sig = new StringBuilder(f).append('|').append(playing0);
        for (Map<String, Object> g : games) sig.append('|').append(Hub.key(g));
        // rebuild only when something in the list changed, so a controller's focus doesn't jump around
        if (sig.toString().equals(listSignature)) return;
        listSignature = sig.toString();
        View focused = getCurrentFocus();
        int focusIndex = focused != null && focused.getParent() == gameList ? gameList.indexOfChild(focused) : -1;
        gameList.removeAllViews();
        if (games.isEmpty()) {
            gameList.addView(Ink.text(this, "No games yet. Tap Game folders and pick the folders where your games are.", 14, false, Ink.INK));
            return;
        }
        String playing = hub.state == Hub.State.QUEUE ? hub.queueKey : hub.playingKey;
        int shown = 0;
        for (final Map<String, Object> g : games) {
            String name = (String) g.get("name");
            if (!f.isEmpty() && !name.toLowerCase(Locale.ROOT).contains(f)) continue;
            if (++shown > 300) break;
            gameList.addView(gameRow(g, Hub.key(g).equals(playing)), fill(shown == 1 ? 0 : 8));
        }
        if (focusIndex >= 0 && gameList.getChildCount() > 0) gameList.getChildAt(Math.min(focusIndex, gameList.getChildCount() - 1)).requestFocus();
    }

    private View gameRow(final Map<String, Object> g, boolean active) {
        Games.Sys s = Hub.sys(g);
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setFocusable(true);
        row.setClickable(true);
        row.setMinimumHeight(dp(64));
        row.setPadding(dp(14), dp(8), dp(12), dp(8));
        row.setBackground(Ink.surface(this, active));
        int fg = active ? Ink.PAPER : Ink.INK;
        LinearLayout names = new LinearLayout(this);
        names.setOrientation(LinearLayout.VERTICAL);
        names.addView(Ink.text(this, (String) g.get("name"), 16, true, fg));
        String sub = s.label + (s.hasNative() ? "" : " · joins a stream");
        names.addView(Ink.text(this, sub, 12.5f, false, active ? 0xFFCFCEC9 : Ink.INK_SOFT));
        row.addView(names, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView go = Ink.text(this, s.hasNative() ? "PLAY" : "QUEUE", 13, true, fg);
        go.setLetterSpacing(0.08f);
        row.addView(go);
        IconView icon = new IconView(this, Icons.PLAY, fg);
        LinearLayout.LayoutParams ip = new LinearLayout.LayoutParams(dp(22), dp(22));
        ip.leftMargin = dp(8);
        row.addView(icon, ip);
        row.setContentDescription(g.get("name") + ", " + s.label + ". Tap for its title card and Play. Press and hold for more.");
        row.setOnClickListener(v -> {
            icon.play();
            status.setText("");
            startActivity(GameActivity.open(this, g));
        });
        row.setOnFocusChangeListener((v, has) -> { if (has && !v.isInTouchMode()) Sfx.play(this, "move"); });
        row.setOnLongClickListener(v -> { gameOptions(g); return true; });
        return row;
    }

    private void gameOptions(final Map<String, Object> g) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        final Dialog[] d = new Dialog[1];
        boolean nat = Hub.sys(g).hasNative();
        if (nat) {
            InkButton solo = new InkButton(this, Icons.PLAY, "Play without the queue", false);
            solo.onPress(() -> { d[0].dismiss(); hub.play(this, g, false); });
            box.addView(solo, fill(0));
            InkButton q = new InkButton(this, Icons.SEARCH, "Queue only (it's already running)", false);
            q.onPress(() -> { d[0].dismiss(); hub.queue(g, true, null, "", "", true); });
            box.addView(q, fill(8));
        }
        InkButton inv = new InkButton(this, Icons.SHARE, "Invite someone with a link", false);
        inv.onPress(() -> { d[0].dismiss(); inviteWithLink(g); });
        box.addView(inv, fill(8));
        InkButton rm = new InkButton(this, Icons.TRASH, "Remove from the list", false);
        rm.onPress(() -> { d[0].dismiss(); removeGame(g); });
        box.addView(rm, fill(8));
        d[0] = Ink.sheet(this, (String) g.get("name"), box, null, null, "Close");
        d[0].show();
    }

    private void refreshChecks() {
        String[] note = new String[4];
        int[] st = new int[4];
        boolean server = !hub.serverHost().isEmpty();
        st[0] = server ? InkHero.READY : InkHero.ATTENTION;
        note[0] = server ? (hub.presenceUp ? "online" : "set") : "not set";
        checkHelp[0] = server ? "The matchmaker address is set. It is where players find each other." + (hub.presenceUp ? " Your friends can see you're online." : "")
                : "Open Settings and enter the matchmaker address your friend gave you.";
        int n = hub.games().size();
        st[1] = n > 0 ? InkHero.READY : InkHero.ATTENTION;
        note[1] = n > 0 ? n + " found" : "add some";
        checkHelp[1] = n > 0 ? "Games are named from their files, so they match the other player's." : "Tap Game folders and pick the folders where your games are.";
        boolean ra = hub.retroArchPackage() != null;
        boolean hk = HotkeyService.isOn(this);
        st[2] = ra ? InkHero.READY : InkHero.ATTENTION;
        note[2] = ra ? (hk ? "shortcut on" : "RetroArch") : "install";
        String pm = prefs.getString("port_message", "");
        checkHelp[2] = (ra ? "RetroArch is installed." : "RetroArch isn't installed. Install it for Game Boy Advance, PS1 and Dreamcast netplay.")
                + (hk ? " The Start + Select shortcut is on." : " The Start + Select shortcut is off (Settings > Run setup again).")
                + (ra && !pm.isEmpty() ? "\n\n" + pm : "");
        boolean parsec = prefs.getBoolean("use_parsec", false);
        st[3] = InkHero.READY;
        note[3] = parsec ? "Parsec" : "built in";
        checkHelp[3] = parsec ? "Parsec: you can join another player's stream. A phone can't host one."
                : "PS2, PS3 and GameCube games stream from a friend's computer. When you match, the stream opens in the app's own player.";
        hero.setChecks(new String[]{"Server", "Games", "Emulator", "Stream"}, note, st);
    }

    private void explainCheck(int i) {
        TextView t = Ink.text(this, checkHelp[i], 14.5f, false, Ink.INK);
        String[] titles = {"Server", "Games", "Emulator", "Stream"};
        Ink.sheet(this, titles[i], t, i == 0 ? "Settings" : "OK", i == 0 ? this::openSettings : null, i == 0 ? "Close" : null).show();
    }

    // ================================================================ games
    @SuppressWarnings("unchecked")
    private void saveGames(List<Map<String, Object>> games) {
        prefs.edit().putString("games", Json.stringify(games)).apply();
        hubChanged();
    }

    private void removeGame(Map<String, Object> g) {
        List<Map<String, Object>> games = hub.games();
        String k = Hub.key(g);
        games.removeIf(x -> Hub.key(x).equals(k));
        saveGames(games);
        status.setText(Boolean.TRUE.equals(g.get("auto"))
                ? "Removed. It comes back the next time your game folders are scanned, unless you remove the file." : "Removed. Your game file isn't deleted.");
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
            List<Map<String, Object>> games = hub.games();
            Map<String, Object> g = new LinkedHashMap<>();
            g.put("name", n);
            g.put("system", systems.get(pickedSys[0]).id);
            g.put("rom", rom.getText().toString().trim());
            games.add(g);
            saveGames(games);
        }, "Cancel").show();
    }

    // ================================================================ links
    private void askForLink() {
        final EditText box = Ink.field(this, "Invite link, friend code or address", "");
        Ink.sheet(this, "Join a link or address", box, "Go", () -> {
            String t = box.getText().toString();
            Friends.Friend f = Friends.parse(t);
            if (f != null && !t.contains("/join")) { addFriendFromLink(f); return; }
            if (Invite.parse(t) == null) {
                String[] addr = net.netplay.core.Lan.parseAddress(t);
                if (addr != null) { connectTo(addr[0], Integer.parseInt(addr[1])); return; }
            }
            joinWithLink(t);
        }, "Cancel").show();
    }

    /** Use a friend's matchmaker by the address they copied from "Your address". */
    private void connectTo(String host, int port) {
        hub.useMatchmaker(host, port);
        status.setText("Connected to the matchmaker at " + host + ":" + port + ". Add each other with friend codes, then Join.");
    }

    private String wifiAddress() {
        try {
            android.net.wifi.WifiManager w = (android.net.wifi.WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            @SuppressWarnings("deprecation") int ip = w.getConnectionInfo().getIpAddress();
            if (ip == 0) return "";
            return (ip & 0xFF) + "." + ((ip >> 8) & 0xFF) + "." + ((ip >> 16) & 0xFF) + "." + ((ip >> 24) & 0xFF);
        } catch (RuntimeException e) { return ""; }
    }

    private void copyText(String label, String text) {
        android.content.ClipboardManager cm = (android.content.ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        cm.setPrimaryClip(android.content.ClipData.newPlainText(label, text));
        status.setText("Copied: " + text);
    }

    private void openAddress() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        final String mm = hub.serverHost();
        final int port = hub.serverPort();
        if (!mm.isEmpty()) {
            box.addView(Ink.caption(this, "Matchmaker you use"));
            box.addView(addressRow(mm + ":" + port, true), fill(4));
            box.addView(Ink.text(this, "Everyone who plays together uses this same one: pass it on.", 12.5f, false, Ink.INK_SOFT), fill(2));
        }
        String home = wifiAddress();
        box.addView(Ink.caption(this, "This phone on your Wi-Fi"), fill(16));
        box.addView(addressRow(home.isEmpty() ? "not on Wi-Fi" : home, !home.isEmpty()), fill(4));
        box.addView(Ink.text(this, "For RetroArch's Connect to Netplay Host when this phone hosts at home.", 12.5f, false, Ink.INK_SOFT), fill(2));
        box.addView(Ink.caption(this, "This phone on the internet"), fill(16));
        final LinearLayout pubRow = new LinearLayout(this);
        pubRow.setOrientation(LinearLayout.VERTICAL);
        box.addView(pubRow, fill(4));
        final TextView asking = Ink.text(this, "Asking the matchmaker...", 14, false, Ink.INK);
        pubRow.addView(asking);
        new Thread(() -> {
            String ip = net.netplay.core.Lan.whoami(mm, port);
            runOnUiThread(() -> {
                pubRow.removeAllViews();
                if (!ip.isEmpty()) { pubRow.addView(addressRow(ip, true)); return; }
                pubRow.addView(Ink.text(this, "Your matchmaker is on your home network, so it can't tell.", 13, false, Ink.INK_SOFT));
                InkButton look = new InkButton(this, Icons.SEARCH, "Look it up online", false);
                look.onPress(() -> {
                    pubRow.removeAllViews();
                    pubRow.addView(Ink.text(this, "Asking online...", 14, false, Ink.INK));
                    new Thread(() -> {
                        String v = net.netplay.core.Lan.lookupOnline();
                        runOnUiThread(() -> {
                            pubRow.removeAllViews();
                            pubRow.addView(v.isEmpty() ? Ink.text(this, "Couldn't find out.", 14, false, Ink.INK) : addressRow(v, true));
                        });
                    }).start();
                });
                pubRow.addView(look, fill(6));
                pubRow.addView(Ink.text(this, "That asks " + net.netplay.core.Lan.LOOKUP_URL + ", which sees your address. Only when you tap it.",
                        12, false, Ink.INK_SOFT), fill(4));
            });
        }).start();
        box.addView(Ink.caption(this, "Got an address from a friend?"), fill(18));
        final EditText field = Ink.field(this, "for example 192.168.1.23 or 203.0.113.5:7777", "");
        box.addView(field, fill(4));
        Ink.sheet(this, "Your address", box, "Connect", () -> {
            String t = field.getText().toString().trim();
            if (t.isEmpty()) return;
            String[] addr = net.netplay.core.Lan.parseAddress(t);
            if (addr == null) status.setText("That isn't an address. It looks like 192.168.1.23 or 203.0.113.5:7777.");
            else connectTo(addr[0], Integer.parseInt(addr[1]));
        }, "Close").show();
    }

    private View addressRow(final String value, boolean copyable) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        TextView v = Ink.text(this, value, 18, true, Ink.INK);
        v.setTextIsSelectable(true);
        row.addView(v, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        if (copyable) {
            InkButton copy = new InkButton(this, Icons.SHARE, "Copy", false);
            copy.onPress(() -> {
                String[] a = net.netplay.core.Lan.parseAddress(value);
                copyText("address", a != null && value.contains(":") ? net.netplay.core.Lan.shareText(a[0], Integer.parseInt(a[1])) : value);
            });
            row.addView(copy);
        }
        return row;
    }

    private void joinWithLink(String text) {
        Invite inv = Invite.parse(text);
        if (inv == null) { status.setText("That doesn't look like a Retro-Netplay link."); return; }
        Map<String, Object> g = hub.gameByKey(inv.game);
        if (g == null) {
            status.setText("You don't have " + hub.gameTitle(inv.game) + " in your games yet. Add its folder, then open the link again.");
            return;
        }
        if (net.netplay.core.Lan.isLoopback(inv.host)) {
            // made by a computer whose matchmaker said 127.0.0.1 (older version): that can't work from here
            if (hub.serverHost().isEmpty()) {
                status.setText("This link points at the other device itself (127.0.0.1), so it can't work from here. "
                        + "Looking for the matchmaker on your home network...");
                hub.findOnNetwork(list -> {
                    if (list.isEmpty()) { status.setText("No matchmaker found on this Wi-Fi. Ask for a new link, or set the address in Settings."); return; }
                    net.netplay.core.Lan.Found b = net.netplay.core.Lan.pick(list);
                    hub.useMatchmaker(b.host, b.port);
                    joinWithLink(text);
                });
                return;
            }
        } else if (!inv.host.equalsIgnoreCase(hub.serverHost()) || inv.port != hub.serverPort()) {
            hub.useMatchmaker(inv.host, inv.port);     // the link's matchmaker is where your friend is waiting
        }
        if (Hub.sys(g).hasNative() && !hub.playingKey.equals(inv.game)) {
            String problem = hub.boot(this, g);
            if (!problem.isEmpty()) { status.setText(problem); return; }
        }
        hub.queue(g, hub.playingKey.equals(inv.game), inv.code, "your friend", "", true);
    }

    /** Old-style invite: a link anyone can open. Friends are easier, but this works with people who aren't. */
    private void inviteWithLink(Map<String, Object> g) {
        if (hub.serverHost().isEmpty()) { status.setText("Looking for Retro-Netplay on your Wi-Fi. Open it on your computer too."); return; }
        String code = Invite.newCode();
        String linkHost = prefs.getString("invite_host", "").trim();
        String link = Invite.link(linkHost.isEmpty() ? hub.serverHost() : linkHost, hub.serverPort(), Hub.key(g), code);
        hub.queue(g, hub.playingKey.equals(Hub.key(g)), code, "", "", true);
        Intent send = new Intent(Intent.ACTION_SEND);
        send.setType("text/plain");
        send.putExtra(Intent.EXTRA_TEXT, "Play " + g.get("name") + " with me! " + link);
        startActivity(Intent.createChooser(send, "Send your invite"));
    }

    // ================================================================ friends
    private void addFriendFromLink(Friends.Friend f) {
        if (!f.lookup.isEmpty()) {
            String problem = hub.lookupCode(f.lookup);
            status.setText(problem.isEmpty() ? "Looking up " + Friends.pretty(f.lookup) + "..." : problem);
            return;
        }
        status.setText(hub.sendRequest(f.id, f.name, ""));
        hubChanged();
    }

    @Override public void friendLookup(String code, boolean found, String id, String name, boolean self) {
        if (!found) { status.setText("No player has the friend code " + code + " on this matchmaker. Check it and try again."); return; }
        if (self) { status.setText("That's your own friend code."); return; }
        status.setText(hub.sendRequest(id, name, code));
        hubChanged();
    }

    // ================================================================ settings
    private void openSettings() {
        final LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(Ink.caption(this, "Your name (friends and opponents see it)"));
        final EditText name = Ink.field(this, "Your name", hub.myName());
        box.addView(name, fill(6));
        box.addView(Ink.caption(this, "Matchmaker address"), fill(12));
        final EditText host = Ink.field(this, "for example 203.0.113.5", prefs.getString("server_host", ""));
        box.addView(host, fill(6));
        box.addView(Ink.caption(this, "Port"), fill(12));
        final EditText port = Ink.field(this, "7777", String.valueOf(prefs.getInt("server_port", 7777)));
        box.addView(port, fill(6));
        final TextView foundNote = Ink.text(this, "", 13, false, Ink.INK_SOFT);
        InkButton findMm = new InkButton(this, Icons.SEARCH, "Find on my network", false);
        findMm.onPress(() -> {
            foundNote.setText("Looking on your home network...");
            hub.findOnNetwork(list -> {
                if (list.isEmpty()) { foundNote.setText("None found on this Wi-Fi. Is the computer's matchmaker on, and on the same Wi-Fi?"); return; }
                net.netplay.core.Lan.Found b = net.netplay.core.Lan.pick(list);
                host.setText(b.host);
                port.setText(String.valueOf(b.port));
                foundNote.setText("Found " + b + ". Press Save.");
            });
        });
        box.addView(findMm, fill(8));
        InkButton addrBtn = new InkButton(this, Icons.LINK, "My address / connect by address", false);
        addrBtn.onPress(this::openAddress);
        box.addView(addrBtn, fill(8));
        box.addView(foundNote, fill(4));
        box.addView(Ink.caption(this, "Public address for links"), fill(12));
        final EditText inviteHost = Ink.field(this, "blank = same as above", prefs.getString("invite_host", ""));
        box.addView(inviteHost, fill(6));
        final InkCheck autoQueue = new InkCheck(this, "Join the queue when I start a game", prefs.getBoolean("auto_queue", true));
        final InkCheck autoPorts = new InkCheck(this, "Open the port on my router automatically (recommended)", prefs.getBoolean("auto_ports", true));
        final InkCheck canHost = new InkCheck(this, "I opened port 55435 on my router myself", prefs.getBoolean("can_host", false));
        final InkCheck useParsec = new InkCheck(this, "Use Parsec instead of built-in streaming (PS2/PS3)", prefs.getBoolean("use_parsec", false));
        final InkCheck useRelay = new InkCheck(this, "Use the matchmaker's relay server when a direct stream connection fails",
                prefs.getBoolean("use_relay", true));
        box.addView(autoQueue, fill(14));
        box.addView(autoPorts, fill(2));
        box.addView(canHost, fill(2));
        box.addView(useParsec, fill(2));
        box.addView(useRelay, fill(2));
        final InkCheck sounds = new InkCheck(this, "Interface sounds (title card, match found)", prefs.getBoolean("sounds", true));
        box.addView(sounds, fill(2));
        InkButton art = new InkButton(this, Icons.DOWNLOAD, "Game art (ScreenScraper)", false);
        art.onPress(() -> ArtDownload.open(this, hub.games(), this::hubChanged));
        box.addView(art, fill(12));
        final Dialog[] me = new Dialog[1];
        InkButton again = new InkButton(this, Icons.CHECK, "Run setup again", false);
        again.onPress(() -> { if (me[0] != null) me[0].dismiss(); setup.start(); });
        box.addView(again, fill(16));
        me[0] = Ink.sheet(this, "Settings", box, "Save", () -> {
            int p = 7777;
            try { p = Integer.parseInt(port.getText().toString().trim()); } catch (NumberFormatException ignored) { }
            boolean serverChanged = !host.getText().toString().trim().equals(hub.serverHost()) || p != hub.serverPort();
            prefs.edit().putString("server_host", host.getText().toString().trim())
                    .putInt("server_port", p).putBoolean("can_host", canHost.isOn())
                    .putString("invite_host", inviteHost.getText().toString().trim())
                    .putBoolean("auto_queue", autoQueue.isOn())
                    .putBoolean("use_parsec", useParsec.isOn())
                    .putBoolean("use_relay", useRelay.isOn())
                    .putBoolean("sounds", sounds.isOn())
                    .putBoolean("auto_ports", autoPorts.isOn()).apply();
            hub.setName(name.getText().toString());
            if (serverChanged) hub.restartPresence();
            status.setText("Settings saved.");
            hubChanged();
            setup.background(true);
        }, "Cancel");
        me[0].show();
    }
}
