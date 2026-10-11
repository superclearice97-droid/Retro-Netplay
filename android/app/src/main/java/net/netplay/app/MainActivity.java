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
    private Dialog openFriends;
    private int buttonMode = -1;
    private String listSignature = "";
    private Runnable friendsRefresher;
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
    }

    @Override protected void onStart() {
        super.onStart();
        hub.addUi(this);
        if (!hub.hasPresence()) hub.restartPresence();
        hubChanged();
        ticker.post(tick);
    }

    @Override protected void onStop() {
        hub.removeUi(this);
        ticker.removeCallbacks(tick);
        // stay visible to friends only while the queue or a game keeps the background service running
        if (hub.state == Hub.State.IDLE && hub.playingKey.isEmpty()) hub.stopPresence();
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
        if (f != null) { addFriendFromLink(f); return; }
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
        streamButton.onPress(() -> { if (!hub.lastStreamUrl.isEmpty()) hub.openUrl(hub.lastStreamUrl); });
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
        friendsButton.onPress(this::openFriends);
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
        friendsButton.set(Icons.SIGNAL, n == 0 ? "Friends" : "Friends  ·  " + on + " of " + n + " online");
        if (friendsRefresher != null) friendsRefresher.run();
    }

    @Override public void say(String message) { status.setText(message); }

    @Override public void incomingCall(Hub.Call c) {
        Ink.sheet(this, c.name, Ink.text(this, c.name + " " + c.what(hub) + ".", 15, false, Ink.INK),
                "Accept", () -> hub.answer(c.id, true), "Decline", () -> hub.answer(c.id, false)).show();
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
            stateText.setText(hub.serverHost().isEmpty() ? "Set the matchmaker address in Settings first." : "Pick a game below.");
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
        row.setContentDescription(g.get("name") + ", " + s.label + ". Tap to play and join the queue. Press and hold for more.");
        row.setOnClickListener(v -> {
            icon.play();
            status.setText("");
            hub.play(this, g, prefs.getBoolean("auto_queue", true));
        });
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
            q.onPress(() -> { d[0].dismiss(); hub.queue(g, true, null, "", "play", true); });
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
                : "PS2, PS3 and GameCube games stream from a friend's computer. When you match, the stream opens in your browser.";
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
        final EditText box = Ink.field(this, "Paste an invite or friend link", "");
        Ink.sheet(this, "Join a link", box, "Go", () -> {
            String t = box.getText().toString();
            Friends.Friend f = Friends.parse(t);
            if (f != null && !t.contains("/join")) addFriendFromLink(f); else joinWithLink(t);
        }, "Cancel").show();
    }

    private void joinWithLink(String text) {
        Invite inv = Invite.parse(text);
        if (inv == null) { status.setText("That doesn't look like a Retro-Netplay link."); return; }
        Map<String, Object> g = hub.gameByKey(inv.game);
        if (g == null) {
            status.setText("You don't have " + hub.gameTitle(inv.game) + " in your games yet. Add its folder, then open the link again.");
            return;
        }
        if (hub.serverHost().isEmpty()) prefs.edit().putString("server_host", inv.host).putInt("server_port", inv.port).apply();
        if (Hub.sys(g).hasNative() && !hub.playingKey.equals(inv.game)) {
            String problem = hub.boot(this, g);
            if (!problem.isEmpty()) { status.setText(problem); return; }
        }
        hub.queue(g, hub.playingKey.equals(inv.game), inv.code, "your friend", "play", true);
    }

    /** Old-style invite: a link anyone can open. Friends are easier, but this works with people who aren't. */
    private void inviteWithLink(Map<String, Object> g) {
        if (hub.serverHost().isEmpty()) { status.setText("Set the matchmaker address in Settings first."); return; }
        String code = Invite.newCode();
        String linkHost = prefs.getString("invite_host", "").trim();
        String link = Invite.link(linkHost.isEmpty() ? hub.serverHost() : linkHost, hub.serverPort(), Hub.key(g), code);
        hub.queue(g, hub.playingKey.equals(Hub.key(g)), code, "", "play", true);
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
        Ink.sheet(this, "Add " + name + "?", Ink.text(this, "Send a friend request to " + name + " (" + code + ")? "
                        + "You'll be friends when they accept.", 14.5f, false, Ink.INK),
                "Send request", () -> { status.setText(hub.sendRequest(id, name, code)); hubChanged(); }, "Cancel").show();
    }

    private void openFriends() {
        final LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(Ink.caption(this, "Your friend code"));
        final TextView code = Ink.text(this, "", 30, true, Ink.INK);
        code.setLetterSpacing(0.08f);
        code.setTextIsSelectable(true);
        box.addView(code, fill(2));
        final TextView me = Ink.text(this, "", 13, false, Ink.INK_SOFT);
        box.addView(me, fill(2));
        LinearLayout r1 = new LinearLayout(this);
        r1.setOrientation(LinearLayout.HORIZONTAL);
        InkButton share = new InkButton(this, Icons.SHARE, "Share my code", true);
        share.onPress(this::shareFriendLink);
        InkButton add = new InkButton(this, Icons.PLUS, "Add a friend", false);
        add.onPress(this::addFriendDialog);
        r1.addView(share, half(0));
        r1.addView(add, half(10));
        box.addView(r1, fill(12));
        final LinearLayout reqs = new LinearLayout(this);
        reqs.setOrientation(LinearLayout.VERTICAL);
        box.addView(reqs, fill(10));
        final LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        box.addView(list, fill(14));
        box.addView(Ink.text(this, "Friends are matched with you first. Tap a friend to play with them or to choose what they may do.",
                12.5f, false, Ink.INK_SOFT), fill(14));
        friendsRefresher = () -> {
            String mine = hub.myCode();
            code.setText(mine.isEmpty() ? "\u00b7\u00b7\u00b7-\u00b7\u00b7\u00b7" : mine);
            me.setText(hub.serverHost().isEmpty() ? "Set the matchmaker address in Settings to get your code."
                    : !hub.presenceUp ? "Connecting to the matchmaker..." : "Name: " + hub.myName() + ". Friends type your code under Add a friend, then you accept.");
            reqs.removeAllViews();
            for (final Map<String, Object> r : hub.requests()) {
                LinearLayout card = new LinearLayout(this);
                card.setOrientation(LinearLayout.VERTICAL);
                card.setPadding(dp(12), dp(10), dp(12), dp(10));
                card.setBackground(Ink.box(Ink.PAPER_DEEP, Ink.INK, 1, this));
                String c = String.valueOf(r.get("code"));
                card.addView(Ink.text(this, r.get("name") + (c.isEmpty() || "null".equals(c) ? "" : " (" + c + ")") + " wants to be friends", 14.5f, true, Ink.INK));
                LinearLayout b = new LinearLayout(this);
                b.setOrientation(LinearLayout.HORIZONTAL);
                final String rid = String.valueOf(r.get("id"));
                InkButton yes = new InkButton(this, Icons.CHECK, "Accept", true);
                yes.onPress(() -> hub.answerRequest(rid, true));
                InkButton no = new InkButton(this, Icons.CLOSE, "Decline", false);
                no.onPress(() -> hub.answerRequest(rid, false));
                b.addView(yes, half(0));
                b.addView(no, half(10));
                card.addView(b, fill(8));
                reqs.addView(card, fill(reqs.getChildCount() == 0 ? 0 : 8));
            }
            list.removeAllViews();
            List<Friends.Friend> fl = hub.friends();
            if (fl.isEmpty()) list.addView(Ink.text(this, "No friends yet. Share your code, or add theirs.", 14, false, Ink.INK));
            for (Friends.Friend f : fl) list.addView(friendRow(f), fill(list.getChildCount() == 0 ? 0 : 8));
        };
        friendsRefresher.run();
        openFriends = Ink.sheet(this, "Friends", box, null, null, "Close", () -> friendsRefresher = null);
        openFriends.setOnDismissListener(d -> friendsRefresher = null);
        openFriends.show();
    }

    private View friendRow(final Friends.Friend f) {
        Map<String, Object> on = hub.onlineInfo(f.id);
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setFocusable(true);
        row.setClickable(true);
        row.setMinimumHeight(dp(60));
        row.setPadding(dp(12), dp(8), dp(12), dp(8));
        row.setBackground(Ink.surface(this, false));
        View dot = new View(this);
        dot.setBackground(Ink.box(on != null ? Ink.spectrum(0.30f) : 0x00000000, Ink.INK, 1, this));
        row.addView(dot, new LinearLayout.LayoutParams(dp(14), dp(14)));
        LinearLayout names = new LinearLayout(this);
        names.setOrientation(LinearLayout.VERTICAL);
        names.setPadding(dp(12), 0, 0, 0);
        names.addView(Ink.text(this, f.name, 15.5f, true, Ink.INK));
        names.addView(Ink.text(this, (f.pending ? "Request sent" : describe(on)) + (f.code.isEmpty() ? "" : " \u00b7 " + f.code)
                + privilegesShort(f), 12.5f, false, Ink.INK_SOFT));
        row.addView(names, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        row.addView(new IconView(this, Icons.CHEVRON, Ink.INK), new LinearLayout.LayoutParams(dp(22), dp(22)));
        row.setOnClickListener(v -> friendSheet(f));
        return row;
    }

    private String describe(Map<String, Object> on) {
        if (on == null) return "Offline";
        String st = String.valueOf(on.get("state")), game = String.valueOf(on.get("game"));
        if ("playing".equals(st) && !game.isEmpty()) return "Playing " + hub.gameTitle(game);
        if ("queue".equals(st) && !game.isEmpty()) return "In the queue for " + hub.gameTitle(game);
        return "Online";
    }

    private static String privilegesShort(Friends.Friend f) {
        List<String> p = new ArrayList<>();
        if (f.priority) p.add("priority");
        if (f.join) p.add("can join");
        if (f.watch) p.add("can watch");
        if (f.slot) p.add("picks slot");
        return p.isEmpty() ? "" : " · " + android.text.TextUtils.join(", ", p);
    }

    private void friendSheet(final Friends.Friend f) {
        final Map<String, Object> on = hub.onlineInfo(f.id);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(Ink.text(this, (f.pending ? "Request sent, waiting for them to accept" : describe(on))
                + (f.code.isEmpty() ? "" : "\nFriend code: " + f.code), 13, false, Ink.INK_SOFT));
        final Dialog[] d = new Dialog[1];
        if (on != null) {
            final String theirGame = String.valueOf(on.get("game"));
            boolean busy = !theirGame.isEmpty() && hub.gameByKey(theirGame) != null;
            InkButton play = new InkButton(this, Icons.PAD, busy ? "Play " + hub.gameTitle(theirGame) + " with them" : "Ask them to play", true);
            play.onPress(() -> {
                d[0].dismiss();
                if (busy) callFriend(f, theirGame, "play");
                else pickGameThen(g -> callFriend(f, Hub.key(g), "play"));
            });
            box.addView(play, fill(14));
            if ("playing".equals(on.get("state")) && busy) {
                InkButton watch = new InkButton(this, Icons.MONITOR, "Watch them play", false);
                watch.onPress(() -> { d[0].dismiss(); callFriend(f, theirGame, "watch"); });
                box.addView(watch, fill(8));
            }
        } else {
            box.addView(Ink.text(this, "When they're online you can ask them to play from here.", 13.5f, false, Ink.INK), fill(10));
        }
        box.addView(Ink.caption(this, "What " + f.name + " may do"), fill(20));
        final InkCheck pri = new InkCheck(this, "Priority: matched with me before anyone else", f.priority);
        final InkCheck join = new InkCheck(this, "Join anytime: can drop into my game without asking", f.join);
        final InkCheck watch = new InkCheck(this, "Watch: can watch my games without asking", f.watch);
        final InkCheck slot = new InkCheck(this, "Pick a controller slot (Player 2-4) on streams I host", f.slot);
        box.addView(pri, fill(8));
        box.addView(join, fill(2));
        box.addView(watch, fill(2));
        box.addView(slot, fill(2));
        InkButton remove = new InkButton(this, Icons.TRASH, "Remove friend", false);
        remove.onPress(() -> {
            d[0].dismiss();
            Ink.sheet(this, "Remove " + f.name + "?", Ink.text(this, "You'll stop seeing each other online. You can add them again later.", 14, false, Ink.INK),
                    "Remove", () -> { hub.removeFriend(f.id); hubChanged(); }, "Cancel").show();
        });
        box.addView(remove, fill(16));
        d[0] = Ink.sheet(this, f.name, box, "Save", () -> {
            f.priority = pri.isOn(); f.join = join.isOn(); f.watch = watch.isOn(); f.slot = slot.isOn();
            hub.updateFriend(f);
            hubChanged();
        }, "Close");
        d[0].show();
    }

    private void callFriend(Friends.Friend f, String gameKey, String kind) {
        String problem = hub.callFriend(f, gameKey, kind);
        if (!problem.isEmpty()) status.setText(problem);
    }

    interface GamePick { void picked(Map<String, Object> g); }

    private void pickGameThen(final GamePick then) {
        List<Map<String, Object>> games = hub.games();
        if (games.isEmpty()) { status.setText("Add your games first."); return; }
        LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        final Dialog[] d = new Dialog[1];
        for (final Map<String, Object> g : games) {
            TextView row = Ink.text(this, g.get("name") + "\n" + Hub.sys(g).label, 15, false, Ink.INK);
            row.setPadding(dp(14), dp(10), dp(14), dp(10));
            row.setMinimumHeight(dp(56));
            row.setFocusable(true);
            row.setClickable(true);
            row.setBackground(Ink.surface(this, false));
            row.setOnClickListener(v -> { d[0].dismiss(); then.picked(g); });
            list.addView(row, fill(list.getChildCount() == 0 ? 0 : 8));
        }
        d[0] = Ink.sheet(this, "Which game?", list, null, null, "Cancel");
        d[0].show();
    }

    private void shareFriendLink() {
        if (hub.serverHost().isEmpty()) { status.setText("Set the matchmaker address in Settings first."); return; }
        String linkHost = prefs.getString("invite_host", "").trim();
        if (hub.myCode().isEmpty()) { status.setText("Still connecting to the matchmaker to get your code. Try again in a moment."); return; }
        String link = Friends.link(linkHost.isEmpty() ? hub.serverHost() : linkHost, hub.serverPort(), hub.myCode(), hub.myName());
        Intent send = new Intent(Intent.ACTION_SEND);
        send.setType("text/plain");
        send.putExtra(Intent.EXTRA_TEXT, "Add me on Retro-Netplay! My friend code: " + hub.myCode() + "\n" + link);
        startActivity(Intent.createChooser(send, "Share your friend link"));
    }

    private void addFriendDialog() {
        final EditText box = Ink.field(this, "Their friend code, like K7M-4QZ", "");
        box.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS
                | android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        box.setTextSize(20);
        Ink.sheet(this, "Add a friend", box, "Look up", () -> {
            Friends.Friend f = Friends.parse(box.getText().toString());
            if (f == null) status.setText("That isn't a friend code. Codes look like K7M-4QZ.");
            else addFriendFromLink(f);
        }, "Cancel").show();
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
        box.addView(Ink.caption(this, "Public address for links"), fill(12));
        final EditText inviteHost = Ink.field(this, "blank = same as above", prefs.getString("invite_host", ""));
        box.addView(inviteHost, fill(6));
        final InkCheck autoQueue = new InkCheck(this, "Join the queue when I start a game", prefs.getBoolean("auto_queue", true));
        final InkCheck friendsOnly = new InkCheck(this, "Only match me with friends", prefs.getBoolean("friends_only", false));
        final InkCheck showOnline = new InkCheck(this, "Let friends see when I'm online and what I'm playing", prefs.getBoolean("show_online", true));
        final InkCheck autoPorts = new InkCheck(this, "Open the port on my router automatically (recommended)", prefs.getBoolean("auto_ports", true));
        final InkCheck canHost = new InkCheck(this, "I opened port 55435 on my router myself", prefs.getBoolean("can_host", false));
        final InkCheck useParsec = new InkCheck(this, "Use Parsec instead of built-in streaming (PS2/PS3)", prefs.getBoolean("use_parsec", false));
        box.addView(autoQueue, fill(14));
        box.addView(friendsOnly, fill(2));
        box.addView(showOnline, fill(2));
        box.addView(autoPorts, fill(2));
        box.addView(canHost, fill(2));
        box.addView(useParsec, fill(2));
        final Dialog[] me = new Dialog[1];
        InkButton again = new InkButton(this, Icons.CHECK, "Run setup again", false);
        again.onPress(() -> { if (me[0] != null) me[0].dismiss(); setup.start(); });
        box.addView(again, fill(16));
        me[0] = Ink.sheet(this, "Settings", box, "Save", () -> {
            int p = 7777;
            try { p = Integer.parseInt(port.getText().toString().trim()); } catch (NumberFormatException ignored) { }
            boolean serverChanged = !host.getText().toString().trim().equals(hub.serverHost()) || p != hub.serverPort()
                    || showOnline.isOn() != prefs.getBoolean("show_online", true);
            prefs.edit().putString("server_host", host.getText().toString().trim())
                    .putInt("server_port", p).putBoolean("can_host", canHost.isOn())
                    .putString("invite_host", inviteHost.getText().toString().trim())
                    .putBoolean("auto_queue", autoQueue.isOn())
                    .putBoolean("friends_only", friendsOnly.isOn())
                    .putBoolean("show_online", showOnline.isOn())
                    .putBoolean("use_parsec", useParsec.isOn())
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
