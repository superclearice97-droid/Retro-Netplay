package net.netplay.app;

import android.app.Activity;
import android.app.Dialog;
import android.content.Context;
import android.content.Intent;
import android.content.res.Configuration;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import net.netplay.core.Friends;
import net.netplay.core.Lan;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Friends, as a full screen: whether you're connected (and how to fix it, including connecting by address),
 * your code, adding a friend by code, requests waiting for you, and your friends grouped into playing now,
 * online and offline, each with Join.
 */
public class FriendsActivity extends Activity implements Hub.Ui {
    private Hub hub;
    private TextView connTitle, connText, myCode, myName, feedback;
    private LinearLayout connActions, requests, list;
    private EditText codeField, addrField;
    private String lastSig = "";

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        hub = Hub.get(this);
        build();
    }

    @Override protected void onStart() {
        super.onStart();
        hub.addUi(this);
        if (!hub.hasPresence()) hub.restartPresence();
        if (!hub.presenceUp) hub.autoFind(hub.serverHost().isEmpty());
        lastSig = "";
        hubChanged();
    }

    @Override protected void onStop() {
        hub.removeUi(this);
        super.onStop();
    }

    @Override public void onConfigurationChanged(Configuration c) {
        super.onConfigurationChanged(c);
        build();
        lastSig = "";
        hubChanged();
    }

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

    private LinearLayout column() {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        c.setPadding(dp(20), dp(16), dp(20), dp(24));
        return c;
    }

    private void build() {
        boolean wide = getResources().getConfiguration().orientation == Configuration.ORIENTATION_LANDSCAPE
                || getResources().getConfiguration().screenWidthDp >= 720;
        LinearLayout left = column(), right = column();

        // --- header
        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        InkButton back = new InkButton(this, Icons.CHEVRON, "Back", false);
        back.onPress(this::finish);
        head.addView(back);
        TextView title = Ink.text(this, "Friends", 22, true, Ink.INK);
        title.setPadding(dp(14), 0, 0, 0);
        head.addView(title);
        left.addView(head);

        // --- connection
        LinearLayout conn = new LinearLayout(this);
        conn.setOrientation(LinearLayout.VERTICAL);
        conn.setPadding(dp(14), dp(12), dp(14), dp(12));
        conn.setBackground(Ink.box(Ink.PAPER_DEEP, Ink.INK, 1, this));
        connTitle = Ink.text(this, "", 15.5f, true, Ink.INK);
        connText = Ink.text(this, "", 13, false, Ink.INK_SOFT);
        conn.addView(connTitle);
        conn.addView(connText, fill(4));
        connActions = new LinearLayout(this);
        connActions.setOrientation(LinearLayout.VERTICAL);
        conn.addView(connActions, fill(8));
        LinearLayout r1 = new LinearLayout(this);
        r1.setOrientation(LinearLayout.HORIZONTAL);
        InkButton retry = new InkButton(this, Icons.SIGNAL, "Try again", true);
        retry.onPress(() -> {
            if (hub.autoOn()) hub.autoFind(true); else hub.restartPresence();
            say("");
        });
        InkButton typeAddr = new InkButton(this, Icons.LINK, "Internet address", false);
        r1.addView(retry, half(0));
        r1.addView(typeAddr, half(8));
        connActions.addView(r1);
        LinearLayout r2 = new LinearLayout(this);
        r2.setOrientation(LinearLayout.HORIZONTAL);
        r2.setGravity(Gravity.CENTER_VERTICAL);
        addrField = Ink.field(this, "Address a friend gave you", "");
        addrField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        addrField.setImeOptions(EditorInfo.IME_ACTION_GO);
        addrField.setOnEditorActionListener((v, id, e) -> { connectByAddress(); return true; });
        r2.addView(addrField, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        InkButton go = new InkButton(this, Icons.LINK, "Connect", true);
        go.onPress(this::connectByAddress);
        LinearLayout.LayoutParams gp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        gp.leftMargin = dp(8);
        r2.addView(go, gp);
        r2.setVisibility(View.GONE);         // only for a friend's matchmaker on the internet; home finds itself
        typeAddr.onPress(() -> r2.setVisibility(r2.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE));
        connActions.addView(r2, fill(8));
        left.addView(conn, fill(14));

        feedback = Ink.text(this, "", 14, true, Ink.INK);
        feedback.setPadding(0, dp(10), 0, 0);
        left.addView(feedback, fill(0));

        // --- you
        left.addView(Ink.caption(this, "Your friend code"), fill(16));
        myCode = Ink.text(this, "", 32, true, Ink.INK);
        myCode.setLetterSpacing(0.08f);
        myCode.setTextIsSelectable(true);
        left.addView(myCode, fill(2));
        myName = Ink.text(this, "", 13, false, Ink.INK_SOFT);
        left.addView(myName, fill(2));
        LinearLayout r3 = new LinearLayout(this);
        r3.setOrientation(LinearLayout.HORIZONTAL);
        InkButton share = new InkButton(this, Icons.SHARE, "Share my code", false);
        share.onPress(this::shareCode);
        r3.addView(share, half(0));
        left.addView(r3, fill(10));

        // --- add a friend (right here, no extra window)
        left.addView(Ink.caption(this, "Add a friend"), fill(18));
        LinearLayout r4 = new LinearLayout(this);
        r4.setOrientation(LinearLayout.HORIZONTAL);
        r4.setGravity(Gravity.CENTER_VERTICAL);
        codeField = Ink.field(this, "Their code, like K7M-4QZ", "");
        codeField.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        codeField.setTextSize(19);
        codeField.setImeOptions(EditorInfo.IME_ACTION_SEND);
        codeField.setOnEditorActionListener((v, id, e) -> { addByCode(); return true; });
        r4.addView(codeField, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        InkButton send = new InkButton(this, Icons.PLUS, "Send request", true);
        send.onPress(this::addByCode);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        sp.leftMargin = dp(8);
        r4.addView(send, sp);
        left.addView(r4, fill(6));

        // --- requests and the list
        requests = new LinearLayout(this);
        requests.setOrientation(LinearLayout.VERTICAL);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        (wide ? right : left).addView(requests, fill(wide ? 0 : 18));
        (wide ? right : left).addView(list, fill(10));

        if (wide) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setBackgroundColor(Ink.PAPER);
            ScrollView a = new ScrollView(this);
            a.addView(left);
            ScrollView c = new ScrollView(this);
            c.addView(right);
            row.addView(a, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f));
            View rule = new View(this);
            rule.setBackgroundColor(Ink.HAIR);
            row.addView(rule, new LinearLayout.LayoutParams(dp(1), ViewGroup.LayoutParams.MATCH_PARENT));
            row.addView(c, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1f));
            setContentView(row);
        } else {
            ScrollView sv = new ScrollView(this);
            sv.setBackgroundColor(Ink.PAPER);
            sv.addView(left);
            setContentView(sv);
        }
    }

    // ================================================================ actions
    private void connectByAddress() {
        String[] a = Lan.parseAddress(addrField.getText().toString());
        if (a == null) { say("That isn't an address. It looks like 192.168.1.23 or 203.0.113.5:7777 (shown on the computer under My address)."); return; }
        hub.useMatchmaker(a[0], Integer.parseInt(a[1]));
        addrField.setText("");
        say("Connecting to " + a[0] + ":" + a[1] + "...");
    }

    private void addByCode() {
        String t = codeField.getText().toString().trim();
        if (t.isEmpty()) { say("Type your friend's code first (they see it at the top of their Friends screen)."); return; }
        Friends.Friend f = Friends.parse(t);
        if (f == null) { say("\"" + t + "\" isn't a friend code. Codes look like K7M-4QZ."); return; }
        if (!f.lookup.isEmpty()) {
            String problem = hub.lookupCode(f.lookup);
            say(problem.isEmpty() ? "Looking up " + Friends.pretty(f.lookup) + "..." : problem);
        } else {
            say(hub.sendRequest(f.id, f.name, ""));
        }
    }

    @Override public void friendLookup(String code, boolean found, String id, String name, boolean self) {
        if (!found) { say("No player has the code " + code + " on this matchmaker (" + hub.serverHost() + "). Check the code, and that both of you use the same matchmaker."); return; }
        if (self) { say("That's your own friend code."); return; }
        codeField.setText("");
        say(hub.sendRequest(id, name, code));
    }

    private void shareCode() {
        if (hub.myCode().isEmpty() || !hub.presenceUp) { say("Connect to a matchmaker first: your code comes from it."); return; }
        String link = Friends.link(hub.serverHost(), hub.serverPort(), hub.myCode(), hub.myName());
        Intent send = new Intent(Intent.ACTION_SEND);
        send.setType("text/plain");
        send.putExtra(Intent.EXTRA_TEXT, "Add me on Retro-Netplay! My friend code: " + hub.myCode()
                + "\nMatchmaker: " + hub.serverHost() + ":" + hub.serverPort() + "\n" + link);
        startActivity(Intent.createChooser(send, "Share your friend code"));
    }

    // ================================================================ Hub.Ui
    @Override public void say(String message) {
        if (feedback != null) feedback.setText(message);
    }

    @Override public void incomingCall(Hub.Call c) {
        Ink.sheet(this, c.name, Ink.text(this, c.name + " " + c.what(hub) + ".", 15, false, Ink.INK),
                "Allow", () -> hub.answer(c.id, true), "Deny", () -> hub.answer(c.id, false)).show();
    }

    @Override public void hubChanged() {
        if (connTitle == null) return;
        String host = hub.serverHost();
        String advice = "Open Retro-Netplay on the computer, on the same Wi-Fi as this device. This keeps looking by itself.";
        if (hub.presenceUp) {
            connTitle.setText("Connected \u00b7 " + hub.serverName());
            connText.setText("Friends on your Wi-Fi connect here by themselves. Nothing to type.");
        } else if (hub.finding) {
            connTitle.setText("Looking for your computer...");
            connText.setText("Make sure Retro-Netplay is open on the computer.");
        } else if (!hub.autoOn()) {
            connTitle.setText("Can't reach " + host + ":" + hub.serverPort());
            String why = hub.presenceProblem;
            connText.setText((why.isEmpty() ? "Connecting... " : why + " ") + "Check the address your friend gave you, or tap Try again.");
        } else if (host.isEmpty() || hub.nothingFound) {
            connTitle.setText("Can't find your computer yet");
            connText.setText(advice);
        } else {
            connTitle.setText("Connecting to " + hub.serverName() + "...");
            String why = hub.presenceProblem;
            connText.setText((why.isEmpty() ? "" : why + " ") + advice);
        }
        connActions.setVisibility(hub.presenceUp ? View.GONE : View.VISIBLE);
        String code = hub.myCode();
        myCode.setText(code.isEmpty() ? "···-···" : code);
        myCode.setAlpha(hub.presenceUp ? 1f : 0.45f);
        myName.setText(hub.presenceUp ? "Name: " + hub.myName() + ". Friends type this code under Add a friend, then you accept."
                : "Your code works once you're connected.");

        // rebuild the lists only when something changed (keeps controller focus steady)
        List<Friends.Friend> fl = hub.friends();
        List<Map<String, Object>> rq = hub.requests();
        StringBuilder sig = new StringBuilder();
        for (Map<String, Object> r : rq) sig.append(r.get("id")).append(',');
        sig.append('|');
        for (Friends.Friend f : fl) {
            Map<String, Object> on = hub.onlineInfo(f.id);
            sig.append(f.id).append(f.pending)
                    .append(on == null ? "-" : on.get("state") + "" + on.get("game") + on.get("can")).append(';');
        }
        if (sig.toString().equals(lastSig)) return;
        lastSig = sig.toString();

        requests.removeAllViews();
        if (!rq.isEmpty()) requests.addView(Ink.caption(this, "Friend requests · " + rq.size()));
        for (final Map<String, Object> r : rq) {
            LinearLayout card = new LinearLayout(this);
            card.setOrientation(LinearLayout.VERTICAL);
            card.setPadding(dp(14), dp(12), dp(14), dp(12));
            card.setBackground(Ink.box(Ink.PAPER_DEEP, Ink.INK, 2, this));
            String c = String.valueOf(r.get("code"));
            card.addView(Ink.text(this, r.get("name") + " wants to be friends", 15.5f, true, Ink.INK));
            if (!c.isEmpty() && !"null".equals(c)) card.addView(Ink.text(this, "Code " + c, 12.5f, false, Ink.INK_SOFT));
            LinearLayout b = new LinearLayout(this);
            b.setOrientation(LinearLayout.HORIZONTAL);
            final String rid = String.valueOf(r.get("id"));
            InkButton yes = new InkButton(this, Icons.CHECK, "Accept", true);
            yes.onPress(() -> hub.answerRequest(rid, true));
            InkButton no = new InkButton(this, Icons.CLOSE, "Decline", false);
            no.onPress(() -> hub.answerRequest(rid, false));
            b.addView(yes, half(0));
            b.addView(no, half(8));
            card.addView(b, fill(10));
            requests.addView(card, fill(8));
        }

        list.removeAllViews();
        List<Friends.Friend> playing = new ArrayList<>(), online = new ArrayList<>(), offline = new ArrayList<>(), pending = new ArrayList<>();
        for (Friends.Friend f : fl) {
            Map<String, Object> on = hub.onlineInfo(f.id);
            if (f.pending) pending.add(f);
            else if (on == null) offline.add(f);
            else if (joinable(on)) playing.add(f);
            else online.add(f);
        }
        if (fl.isEmpty()) {
            list.addView(Ink.caption(this, "Your friends"), fill(rq.isEmpty() ? 0 : 18));
            list.addView(Ink.text(this, "No friends yet. Share your code, or type a friend's code under Add a friend. "
                    + "Then tap Join next to them: they allow it, and you connect to their game.", 14, false, Ink.INK), fill(6));
        }
        section("Playing now", playing);
        section("Online", online);
        section("Request sent", pending);
        section("Offline", offline);
    }

    private static boolean joinable(Map<String, Object> on) {
        String g = String.valueOf(on.get("game"));
        return !g.isEmpty() && !"null".equals(g) && ("playing".equals(on.get("state")) || "queue".equals(on.get("state")));
    }

    private void section(String title, List<Friends.Friend> fs) {
        if (fs.isEmpty()) return;
        list.addView(Ink.caption(this, title + "  ·  " + fs.size()), fill(list.getChildCount() == 0 ? 0 : 18));
        for (Friends.Friend f : fs) list.addView(row(f), fill(8));
    }

    private View row(final Friends.Friend f) {
        Map<String, Object> on = hub.onlineInfo(f.id);
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setFocusable(true);
        row.setClickable(true);
        row.setMinimumHeight(dp(64));
        row.setPadding(dp(14), dp(8), dp(12), dp(8));
        row.setBackground(Ink.surface(this, false));
        View dot = new View(this);
        dot.setBackground(Ink.box(on != null ? Ink.spectrum(0.30f) : 0x00000000, Ink.INK, 1, this));
        row.addView(dot, new LinearLayout.LayoutParams(dp(14), dp(14)));
        LinearLayout names = new LinearLayout(this);
        names.setOrientation(LinearLayout.VERTICAL);
        names.setPadding(dp(12), 0, 0, 0);
        names.addView(Ink.text(this, f.name, 16, true, Ink.INK));
        String state = f.pending ? "Waiting for them to accept" : describe(on);
        names.addView(Ink.text(this, state + (f.code.isEmpty() ? "" : " · " + f.code), 12.5f, false, Ink.INK_SOFT));
        row.addView(names, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        if (!f.pending && on != null) {
            InkButton join = new InkButton(this, Icons.PLAY, "Join", true);
            join.setContentDescription(joinable(on) ? "Join " + f.name + " in " + hub.gameTitle(String.valueOf(on.get("game"))) : "Join " + f.name);
            join.onPress(() -> join(f));
            row.addView(join);
        } else {
            row.addView(new IconView(this, Icons.CHEVRON, Ink.INK), new LinearLayout.LayoutParams(dp(22), dp(22)));
        }
        row.setOnClickListener(v -> friendSheet(f));
        return row;
    }

    /** Join a friend: their game if they're in one, else pick one of yours. They tap Allow, then they host. */
    private void join(final Friends.Friend f) {
        Map<String, Object> on = hub.onlineInfo(f.id);
        if (on == null) { say(f.name + " isn't online right now."); return; }
        if (joinable(on)) {
            String p = hub.callFriend(f, String.valueOf(on.get("game")));
            if (!p.isEmpty()) say(p);
            return;
        }
        pickGameThen(g -> { String p = hub.callFriend(f, Hub.key(g)); if (!p.isEmpty()) say(p); });
    }

    interface GamePick { void picked(Map<String, Object> g); }

    private void pickGameThen(final GamePick then) {
        List<Map<String, Object>> games = hub.games();
        if (games.isEmpty()) { say("Add your games first."); return; }
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        final Dialog[] d = new Dialog[1];
        for (final Map<String, Object> g : games) {
            TextView row = Ink.text(this, g.get("name") + "\n" + Hub.sys(g).label, 15, false, Ink.INK);
            row.setPadding(dp(14), dp(10), dp(14), dp(10));
            row.setMinimumHeight(dp(56));
            row.setFocusable(true);
            row.setClickable(true);
            row.setBackground(Ink.surface(this, false));
            row.setOnClickListener(v -> { d[0].dismiss(); then.picked(g); });
            box.addView(row, fill(box.getChildCount() == 0 ? 0 : 8));
        }
        ScrollView sv = new ScrollView(this);
        sv.addView(box);
        d[0] = Ink.sheet(this, "Which game?", sv, null, null, "Cancel");
        d[0].show();
    }

    private String describe(Map<String, Object> on) {
        if (on == null) return "Offline";
        String st = String.valueOf(on.get("state")), game = String.valueOf(on.get("game"));
        if ("playing".equals(st) && !game.isEmpty()) return "Playing " + hub.gameTitle(game);
        if ("queue".equals(st) && !game.isEmpty()) return "Playing " + hub.gameTitle(game);
        return "Online";
    }

    private void friendSheet(final Friends.Friend f) {
        final Map<String, Object> on = hub.onlineInfo(f.id);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.addView(Ink.text(this, (f.pending ? "Request sent, waiting for them to accept" : describe(on))
                + (f.code.isEmpty() ? "" : "\nFriend code: " + f.code), 13, false, Ink.INK_SOFT));
        final Dialog[] d = new Dialog[1];
        if (!f.pending && on != null) {
            String label = joinable(on) ? "Join " + hub.gameTitle(String.valueOf(on.get("game"))) : "Join (pick a game)";
            InkButton join = new InkButton(this, Icons.PAD, label, true);
            join.onPress(() -> { d[0].dismiss(); join(f); });
            box.addView(join, fill(14));
            box.addView(Ink.text(this, f.name + " gets Allow / Deny. Once they allow it, they host and you connect.", 12.5f, false, Ink.INK_SOFT), fill(6));
        } else if (!f.pending) {
            box.addView(Ink.text(this, "When they're online you can join them from here.", 13.5f, false, Ink.INK), fill(10));
        }
        InkButton remove = new InkButton(this, Icons.TRASH, "Remove friend", false);
        remove.onPress(() -> {
            d[0].dismiss();
            Ink.sheet(this, "Remove " + f.name + "?", Ink.text(this, "You'll stop seeing each other online. You can add them again later.", 14, false, Ink.INK),
                    "Remove", () -> { hub.removeFriend(f.id); lastSig = ""; hubChanged(); }, "Cancel").show();
        });
        box.addView(remove, fill(16));
        d[0] = Ink.sheet(this, f.name, box, null, null, "Close");
        d[0].show();
    }

    @Override public boolean onKeyDown(int keyCode, KeyEvent e) {
        if (keyCode == KeyEvent.KEYCODE_BUTTON_B) { finish(); return true; }     // B goes back, like a console menu
        return super.onKeyDown(keyCode, e);
    }

    static void open(Context c) { c.startActivity(new Intent(c, FriendsActivity.class)); }

    @SuppressWarnings("unused")
    private void toast(String s) { Toast.makeText(this, s, Toast.LENGTH_SHORT).show(); }
}
