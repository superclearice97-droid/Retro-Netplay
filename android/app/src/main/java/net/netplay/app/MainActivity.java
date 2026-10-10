package net.netplay.app;

import android.app.Activity;
import android.app.AlertDialog;
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
import android.view.View;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
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

/** One screen: pick a game, tap the big button, get matched with a desktop or phone player. */
public class MainActivity extends Activity {
    private static final String[] RETROARCH_PACKAGES = {"com.retroarch.aarch64", "com.retroarch", "com.retroarch.ra32"};

    private SharedPreferences prefs;
    private final List<Map<String, Object>> games = new ArrayList<>();
    private Spinner gameSpinner;
    private Button goButton;
    private TextView status, checks;
    private MatchClient running;   // non-null while searching
    private TextView linkView;
    private Button shareButton;
    private String currentLink = "";
    private String currentGameName = "";

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("nm", Context.MODE_PRIVATE);
        loadGames();
        buildUi();
        refresh();
        if (prefs.getString("server_host", "").isEmpty()) {
            status.setText("First, open Settings and enter the matchmaker address your friend gave you.");
        }
        handleLink(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleLink(intent);
    }

    /** Someone tapped an invite link (netplay://join?...) in a browser or chat. */
    private void handleLink(Intent intent) {
        Uri data = intent == null ? null : intent.getData();
        if (data != null) joinWithLink(data.toString());
    }

    // ------------------------------------------------------------ UI
    private int dp(int v) { return (int) (v * getResources().getDisplayMetrics().density); }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(24), dp(16), dp(16));
        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        setContentView(scroll);

        TextView title = new TextView(this);
        title.setText("Netplay Matchmaker");
        title.setTextSize(22);
        root.addView(title);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        gameSpinner = new Spinner(this);
        row.addView(gameSpinner, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        Button add = new Button(this);
        add.setText("Add");
        add.setOnClickListener(v -> addGame());
        row.addView(add);
        Button remove = new Button(this);
        remove.setText("Remove");
        remove.setOnClickListener(v -> removeGame());
        row.addView(remove);
        root.addView(row);
        gameSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> parent, View view, int pos, long id) { refreshChecks(); }
            @Override public void onNothingSelected(AdapterView<?> parent) { }
        });

        goButton = new Button(this);
        goButton.setText("Find an opponent");
        goButton.setTextSize(18);
        goButton.setPadding(dp(8), dp(20), dp(8), dp(20));
        goButton.setOnClickListener(v -> { if (running != null) running.cancel(); else search(null); });
        root.addView(goButton);

        status = new TextView(this);
        status.setTextSize(16);
        status.setPadding(0, dp(16), 0, dp(8));
        root.addView(status);

        linkView = new TextView(this);
        linkView.setVisibility(View.GONE);
        root.addView(linkView);
        shareButton = new Button(this);
        shareButton.setText("Share invite link");
        shareButton.setVisibility(View.GONE);
        shareButton.setOnClickListener(v -> shareLink());
        root.addView(shareButton);

        checks = new TextView(this);
        checks.setPadding(0, 0, 0, dp(16));
        root.addView(checks);

        Button join = new Button(this);
        join.setText("Join with a link");
        join.setOnClickListener(v -> askForLink());
        root.addView(join);

        Button settings = new Button(this);
        settings.setText("Settings");
        settings.setOnClickListener(v -> openSettings());
        root.addView(settings);
    }

    private void refresh() {
        List<String> names = new ArrayList<>();
        for (Map<String, Object> g : games) {
            names.add(g.get("name") + "  (" + Games.SYSTEMS.get((String) g.get("system")).label + ")");
        }
        ArrayAdapter<String> ad = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, names);
        ad.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        gameSpinner.setAdapter(ad);
        int sel = Math.min(prefs.getInt("last_game", 0), Math.max(0, names.size() - 1));
        if (!names.isEmpty()) gameSpinner.setSelection(sel);
        refreshChecks();
    }

    private Map<String, Object> currentGame() {
        int i = gameSpinner.getSelectedItemPosition();
        return (i >= 0 && i < games.size()) ? games.get(i) : null;
    }

    private void refreshChecks() {
        Map<String, Object> g = currentGame();
        StringBuilder sb = new StringBuilder();
        if (g == null) {
            sb.append("Tap Add and enter a game. Your opponent must add the same game with the exact same name.");
        } else {
            Games.Sys sys = Games.SYSTEMS.get((String) g.get("system"));
            prefs.edit().putInt("last_game", gameSpinner.getSelectedItemPosition()).apply();
            if (sys.hasNative()) {
                sb.append(retroArchPackage() != null ? "OK  RetroArch found\n"
                        : "TIP  RetroArch isn't installed. Install it for rollback netplay. Streaming still works.\n");
                sb.append(((String) g.get("rom")).isEmpty() ? "TIP  No game file set. Netplay needs one; streaming doesn't.\n" : "OK  Game file set\n");
            } else {
                sb.append("OK  " + sys.label + " games play by joining someone's Parsec stream.\n");
            }
            sb.append("OK  Parsec: you can join another player's stream (a phone can't host one).\n");
            sb.append("Your opponent must add this game as \"" + g.get("name") + "\".");
        }
        checks.setText(sb.toString());
    }

    // ------------------------------------------------------------ games
    @SuppressWarnings("unchecked")
    private void loadGames() {
        games.clear();
        try {
            Object o = Json.parse(prefs.getString("games", "[]"));
            for (Object x : (List<Object>) o) games.add((Map<String, Object>) x);
        } catch (RuntimeException ignored) { }
    }

    private void saveGames() { prefs.edit().putString("games", Json.stringify(games)).apply(); }

    private void addGame() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(8), dp(20), 0);
        final EditText name = new EditText(this);
        name.setHint("Game name (your opponent types the same)");
        box.addView(name);
        final Spinner sys = new Spinner(this);
        ArrayAdapter<Games.Sys> ad = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, new ArrayList<>(Games.SYSTEMS.values()));
        ad.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        sys.setAdapter(ad);
        box.addView(sys);
        final EditText rom = new EditText(this);
        rom.setHint("Game file path, e.g. /storage/emulated/0/ROMs/game.gba (blank for PS2/PS3)");
        box.addView(rom);
        new AlertDialog.Builder(this).setTitle("Add game").setView(box)
                .setPositiveButton("Add", (d, w) -> {
                    String n = name.getText().toString().trim();
                    if (n.isEmpty()) return;
                    Map<String, Object> g = new LinkedHashMap<>();
                    g.put("name", n);
                    g.put("system", ((Games.Sys) sys.getSelectedItem()).id);
                    g.put("rom", rom.getText().toString().trim());
                    games.add(g);
                    saveGames();
                    refresh();
                    gameSpinner.setSelection(games.size() - 1);
                })
                .setNegativeButton("Cancel", null).show();
    }

    private void removeGame() {
        int i = gameSpinner.getSelectedItemPosition();
        if (i >= 0 && i < games.size()) {
            games.remove(i);
            saveGames();
            refresh();
        }
    }

    // ------------------------------------------------------------ settings
    private void openSettings() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(8), dp(20), 0);
        final EditText host = new EditText(this);
        host.setHint("Matchmaker address");
        host.setText(prefs.getString("server_host", ""));
        box.addView(host);
        final EditText port = new EditText(this);
        port.setHint("Port (default 7777)");
        port.setText(String.valueOf(prefs.getInt("server_port", 7777)));
        box.addView(port);
        final CheckBox canHost = new CheckBox(this);
        canHost.setText("I can host netplay (home Wi-Fi, RetroArch port 55435 open)");
        canHost.setChecked(prefs.getBoolean("can_host", false));
        box.addView(canHost);
        final EditText inviteHost = new EditText(this);
        inviteHost.setHint("Public address for invite links (blank = same as above)");
        inviteHost.setText(prefs.getString("invite_host", ""));
        box.addView(inviteHost);
        final CheckBox inviteOn = new CheckBox(this);
        inviteOn.setText("Make an invite link when I search");
        inviteOn.setChecked(prefs.getBoolean("invite_on", true));
        box.addView(inviteOn);
        final CheckBox randoms = new CheckBox(this);
        randoms.setText("Also let random players match me");
        randoms.setChecked(prefs.getBoolean("invite_randoms", true));
        box.addView(randoms);
        final CheckBox autoShare = new CheckBox(this);
        autoShare.setText("Open the share sheet when I search");
        autoShare.setChecked(prefs.getBoolean("auto_share", true));
        box.addView(autoShare);
        final CheckBox useParsec = new CheckBox(this);
        useParsec.setText("Use Parsec instead of built-in streaming (PS2/PS3)");
        useParsec.setChecked(prefs.getBoolean("use_parsec", false));
        box.addView(useParsec);
        new AlertDialog.Builder(this).setTitle("Settings").setView(box)
                .setPositiveButton("Save", (d, w) -> {
                    int p = 7777;
                    try { p = Integer.parseInt(port.getText().toString().trim()); } catch (NumberFormatException ignored) { }
                    prefs.edit().putString("server_host", host.getText().toString().trim())
                            .putInt("server_port", p).putBoolean("can_host", canHost.isChecked())
                            .putString("invite_host", inviteHost.getText().toString().trim())
                            .putBoolean("invite_on", inviteOn.isChecked())
                            .putBoolean("invite_randoms", randoms.isChecked())
                            .putBoolean("use_parsec", useParsec.isChecked())
                            .putBoolean("auto_share", autoShare.isChecked()).apply();
                    status.setText("Settings saved.");
                })
                .setNegativeButton("Cancel", null).show();
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
        final Plan.Offer offer = Plan.offer(sys, retroArchPackage() != null, !rom.isEmpty(), prefs.getBoolean("can_host", false));
        final String key = Games.key(sys.id, (String) g.get("name"));
        final MatchClient mc = new MatchClient();
        running = mc;
        goButton.setText("Cancel search");
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
        final EditText box = new EditText(this);
        box.setHint("Paste the invite link");
        new AlertDialog.Builder(this).setTitle("Join with a link").setView(box)
                .setPositiveButton("Join", (d, w) -> joinWithLink(box.getText().toString()))
                .setNegativeButton("Cancel", null).show();
    }

    private void joinWithLink(String text) {
        Invite inv = Invite.parse(text);
        if (inv == null) { status.setText("That doesn't look like a Netplay Matchmaker invite link."); return; }
        search(inv);
    }

    private void finishSearch(String message) {
        running = null;
        goButton.setText("Find an opponent");
        status.setText(message);
        currentLink = "";
        linkView.setVisibility(View.GONE);
        shareButton.setVisibility(View.GONE);
    }

    private void onMatch(Games.Sys sys, String rom, MatchClient.Match m, String host, int port) {
        // Built-in stream: the guest page lives on the matchmaker's web port (TCP port + 1).
        String playUrl = Plan.playUrl(host, port, m.play);
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
