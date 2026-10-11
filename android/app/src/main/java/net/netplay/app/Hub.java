package net.netplay.app;

import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;

import net.netplay.core.Friends;
import net.netplay.core.Games;
import net.netplay.core.Json;
import net.netplay.core.Library;
import net.netplay.core.MatchClient;
import net.netplay.core.Plan;
import net.netplay.core.Presence;
import net.netplay.core.RetroCmd;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * The app's brain, shared by the screen, the background service, the notification buttons and the controller
 * shortcut: the queue, the match, friends online and calls. One per process.
 */
final class Hub {
    enum State { IDLE, QUEUE }

    interface Ui {
        void hubChanged();
        void incomingCall(Call c);
        void say(String message);
        /** Answer to a friend-code lookup you started. */
        default void friendLookup(String code, boolean found, String id, String name, boolean self) { }
    }

    static final class Call {
        final String from, name, game, kind, id;
        Call(String from, String name, String game, String kind, String id) {
            this.from = from; this.name = name; this.game = game; this.kind = kind; this.id = id;
        }
        String what(Hub h) {
            return (kind.equals("watch") ? "wants to watch you play " : "wants to play ") + h.gameTitle(game);
        }
    }

    static final String[] RETROARCH_PACKAGES = {"com.retroarch.aarch64", "com.retroarch", "com.retroarch.ra32"};

    private static Hub instance;

    static synchronized Hub get(Context c) {
        if (instance == null) instance = new Hub(c.getApplicationContext());
        return instance;
    }

    final Context app;
    final SharedPreferences prefs;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService work = Executors.newSingleThreadExecutor();
    private final List<Ui> uis = new ArrayList<>();

    // queue
    volatile State state = State.IDLE;
    volatile String queueKey = "", queueName = "", queueWith = "";
    volatile long queueSince;
    volatile String message = "";
    volatile String playingKey = "", playingWith = "";
    volatile String lastStreamUrl = "";
    private volatile MatchClient search;
    private volatile boolean hostingNow;
    volatile boolean hotkeyTest;
    volatile Runnable onHotkeyTest;

    // friends
    private Presence presence;
    volatile boolean presenceUp;
    final Map<String, Map<String, Object>> online = new LinkedHashMap<>();
    final List<Call> calls = new ArrayList<>();
    private String outgoingCall = "", outgoingTo = "";

    private Hub(Context c) {
        app = c;
        prefs = c.getSharedPreferences("nm", Context.MODE_PRIVATE);
        if (!Friends.SECRET.matcher(prefs.getString("secret", "")).matches()) {
            prefs.edit().putString("secret", Friends.newSecret()).apply();
        }
        if (prefs.getString("my_name", "").isEmpty()) {
            prefs.edit().putString("my_name", "Player " + myId().substring(0, 4).toUpperCase()).apply();
        }
    }

    // ================================================================ listeners
    void addUi(Ui u) { synchronized (uis) { if (!uis.contains(u)) uis.add(u); } }
    void removeUi(Ui u) { synchronized (uis) { uis.remove(u); } }

    private List<Ui> uiList() { synchronized (uis) { return new ArrayList<>(uis); } }

    void changed() {
        main.post(() -> {
            for (Ui u : uiList()) u.hubChanged();
            QueueService.refresh(app);
        });
    }

    /** A message for the person: on the screen if it's open, on the game screen if RetroArch is running. */
    void say(String msg, boolean inGame) {
        message = msg;
        if (inGame) work.execute(() -> RetroCmd.showMsg("Retro-Netplay: " + msg));
        main.post(() -> { for (Ui u : uiList()) u.say(msg); });
        changed();
    }

    void buzz(long ms) {
        try {
            Vibrator v = (Vibrator) app.getSystemService(Context.VIBRATOR_SERVICE);
            if (v == null) return;
            if (android.os.Build.VERSION.SDK_INT >= 26) v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE));
            else v.vibrate(ms);
        } catch (RuntimeException ignored) { }
    }

    // ================================================================ identity and friends
    String secret() { return prefs.getString("secret", ""); }
    String myId() { return Friends.idOf(secret()); }
    String myName() { return prefs.getString("my_name", "Player"); }

    void setName(String n) {
        String c = Friends.cleanName(n);
        if (c.isEmpty() || c.equals(myName())) return;
        prefs.edit().putString("my_name", c).apply();
        restartPresence();
    }

    List<Friends.Friend> friends() { return Friends.load(prefs.getString("friends", "[]")); }

    void saveFriends(List<Friends.Friend> list) {
        prefs.edit().putString("friends", Friends.save(list)).apply();
    }

    String myCode() { return prefs.getString("my_code", ""); }

    // ---------------------------------------------------------------- friend requests
    @SuppressWarnings("unchecked")
    List<Map<String, Object>> requests() {
        List<Map<String, Object>> out = new ArrayList<>();
        try {
            for (Object o : (List<Object>) Json.parse(prefs.getString("friend_requests", "[]")))
                if (o instanceof Map && Friends.ID.matcher(String.valueOf(((Map<?, ?>) o).get("id"))).matches()) out.add((Map<String, Object>) o);
        } catch (RuntimeException ignored) { }
        return out;
    }

    private void saveRequests(List<Map<String, Object>> r) {
        prefs.edit().putString("friend_requests", Json.stringify(r)).apply();
    }

    /** Ask the matchmaker who has this code; the answer arrives at Ui.friendLookup. "" or a reason it can't. */
    String lookupCode(String code) {
        Presence p = presence;
        if (p == null || !presenceUp) return serverHost().isEmpty() ? "Set the matchmaker address in Settings first." : "Still connecting to the matchmaker. Try again in a moment.";
        p.lookup(code);
        return "";
    }

    /** Add them (marked "request sent") and send the request. Returns a message for the person. */
    String sendRequest(String id, String name, String code) {
        if (id.equals(myId())) return "That's your own friend code.";
        List<Friends.Friend> l = friends();
        Friends.Friend f = Friends.find(l, id);
        if (f != null && !f.pending) return f.name + " is already your friend.";
        if (f == null) {
            f = new Friends.Friend(id, name);
            f.code = Friends.pretty(code);
            f.pending = true;
            l.add(f);
            saveFriends(l);
        }
        List<Map<String, Object>> r = requests();
        boolean theyAsked = r.removeIf(x -> id.equals(x.get("id")));
        if (theyAsked) { f.pending = false; updateFriend(f); saveRequests(r); }
        Presence p = presence;
        if (p != null) { if (theyAsked) p.accept(id); else p.request(id); }
        restartPresence();
        return theyAsked ? "You and " + f.name + " are friends now." : "Friend request sent to " + f.name + ". You'll be friends when they accept.";
    }

    void answerRequest(String id, boolean ok) {
        List<Map<String, Object>> r = requests();
        Map<String, Object> req = null;
        for (Map<String, Object> x : r) if (id.equals(x.get("id"))) req = x;
        if (req == null) return;
        r.remove(req);
        saveRequests(r);
        if (ok) {
            List<Friends.Friend> l = friends();
            if (Friends.find(l, id) == null) {
                Friends.Friend f = new Friends.Friend(id, String.valueOf(req.get("name")));
                f.code = Friends.pretty(String.valueOf(req.get("code")));
                l.add(f);
                saveFriends(l);
            }
            Presence p = presence;
            if (p != null) p.accept(id);
            restartPresence();
            say("You and " + Friends.cleanName(String.valueOf(req.get("name"))) + " are friends now.", false);
        }
        changed();
    }

    private void gotRequest(String id, String name, String code) {
        List<Friends.Friend> l = friends();
        Friends.Friend f = Friends.find(l, id);
        if (f != null) {                       // you'd already asked them: friends now
            f.pending = false;
            if (!code.isEmpty()) f.code = code;
            saveFriends(l);
            Presence p = presence;
            if (p != null) p.accept(id);
            restartPresence();
            say("You and " + f.name + " are friends now.", false);
            return;
        }
        List<Map<String, Object>> r = requests();
        for (Map<String, Object> x : r) if (id.equals(x.get("id"))) return;
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", id); m.put("name", name); m.put("code", code);
        r.add(m);
        if (r.size() > 50) r.remove(0);
        saveRequests(r);
        buzz(150);
        Notify.info(app, name + " wants to be friends", "Open Friends in Retro-Netplay to accept." + (code.isEmpty() ? "" : " Their code: " + code));
        say(name + " wants to be friends. Open Friends to accept.", false);
    }

    /** Returns false if it's you or already a friend. */
    boolean addFriend(Friends.Friend f) {
        if (f.id.equals(myId())) return false;
        List<Friends.Friend> l = friends();
        Friends.Friend old = Friends.find(l, f.id);
        if (old != null) {
            if (!f.name.equals("Friend")) { old.name = f.name; saveFriends(l); }
            return false;
        }
        l.add(f);
        saveFriends(l);
        restartPresence();
        return true;
    }

    void removeFriend(String id) {
        List<Friends.Friend> l = friends();
        Friends.Friend f = Friends.find(l, id);
        if (f != null) { l.remove(f); saveFriends(l); restartPresence(); }
    }

    void updateFriend(Friends.Friend f) {
        List<Friends.Friend> l = friends();
        for (int i = 0; i < l.size(); i++) if (l.get(i).id.equals(f.id)) l.set(i, f);
        saveFriends(l);
    }

    String serverHost() { return prefs.getString("server_host", "").trim(); }
    int serverPort() { return prefs.getInt("server_port", 7777); }

    /** Visible to friends (and reachable for calls) while the app or the queue is running. */
    synchronized void restartPresence() {
        if (presence != null) { presence.stop(); presence = null; }
        presenceUp = false;
        synchronized (online) { online.clear(); }
        List<Friends.Friend> fl = friends();
        // connected whenever a matchmaker is set, so friend requests reach you; "show online" only hides you
        if (serverHost().isEmpty()) { changed(); return; }
        presence = new Presence(serverHost(), serverPort(), secret(), myName(), Friends.ids(fl), new Presence.Listener() {
            @Override public void onMyCode(String code) {
                if (!code.isEmpty() && !code.equals(myCode())) prefs.edit().putString("my_code", code).apply();
                changed();
            }
            @Override public void onLookup(String code, boolean found, String id, String name, boolean self) {
                main.post(() -> { for (Ui u : uiList()) u.friendLookup(code, found, id, name, self); });
            }
            @Override public void onFriendRequest(String id, String name, String code) { gotRequest(id, name, code); }
            @Override public void onFriendAccepted(String id, String name, String code) {
                List<Friends.Friend> l = friends();
                Friends.Friend f = Friends.find(l, id);
                if (f == null) return;
                f.pending = false;
                if (!code.isEmpty()) f.code = code;
                saveFriends(l);
                say(f.name + " accepted your friend request.", false);
                restartPresence();
            }
            @Override public void onSoftError(String msg) { say("Friends: " + msg, false); }
            @Override public void onConnected(boolean c, String why) {
                presenceUp = c;
                if (!c) synchronized (online) { online.clear(); }
                changed();
            }
            @Override public void onOnline(List<Map<String, Object>> list) {
                synchronized (online) {
                    online.clear();
                    for (Map<String, Object> m : list) online.put(String.valueOf(m.get("id")), m);
                }
                // listed online means you both added each other
                List<Friends.Friend> l = friends();
                boolean dirty = false;
                for (Friends.Friend f : l) {
                    Map<String, Object> m = onlineInfo(f.id);
                    if (m == null) continue;
                    String c = Friends.pretty(String.valueOf(m.get("code")));
                    if (f.pending || (!c.isEmpty() && !c.equals(f.code))) { f.pending = false; if (!c.isEmpty()) f.code = c; dirty = true; }
                }
                if (dirty) saveFriends(l);
                changed();
            }
            @Override public void onCall(String from, String name, String game, String kind, String id) { incoming(new Call(from, name, game, kind, id)); }
            @Override public void onStart(String id, String code, String game, String kind, String as, String withId, String withName) {
                started(code, game, kind, as, withName);
            }
            @Override public void onDeclined(String id, String name) {
                outgoingCall = "";
                say((name.isEmpty() ? "Your friend" : name) + " can't play right now.", false);
            }
            @Override public void onCallFailed(String id, String msg) {
                outgoingCall = "";
                say(msg.equals("not online") ? "That friend isn't online right now." : "The call didn't go through: " + msg, false);
            }
            @Override public void onCallCancelled(String id, String from) {
                synchronized (calls) { calls.removeIf(c -> c.id.equals(id)); }
                Notify.cancelCall(app, id);
                changed();
            }
        }, !prefs.getBoolean("show_online", true));
        presence.start();
        sendStatus();
    }

    void stopPresence() {
        synchronized (this) { if (presence != null) { presence.stop(); presence = null; } }
        presenceUp = false;
    }

    boolean hasPresence() { return presence != null; }

    private void sendStatus() {
        Presence p = presence;
        if (p == null) return;
        if (state == State.QUEUE) p.status("queue", queueKey);
        else if (!playingKey.isEmpty()) p.status("playing", playingKey);
        else p.status("idle", "");
    }

    Map<String, Object> onlineInfo(String id) { synchronized (online) { return online.get(id); } }

    // ================================================================ games
    @SuppressWarnings("unchecked")
    List<Map<String, Object>> games() {
        List<Map<String, Object>> out = new ArrayList<>();
        try {
            for (Object x : (List<Object>) Json.parse(prefs.getString("games", "[]"))) {
                if (!(x instanceof Map)) continue;
                Map<String, Object> g = (Map<String, Object>) x;
                if (!(g.get("name") instanceof String) || !(g.get("system") instanceof String)
                        || !Games.SYSTEMS.containsKey(g.get("system"))) continue;
                if (!(g.get("rom") instanceof String)) g.put("rom", "");
                out.add(g);
            }
        } catch (RuntimeException ignored) { }
        return out;
    }

    static String key(Map<String, Object> g) { return Games.key((String) g.get("system"), (String) g.get("name")); }
    static Games.Sys sys(Map<String, Object> g) { return Games.SYSTEMS.get((String) g.get("system")); }

    Map<String, Object> gameByKey(String key) {
        for (Map<String, Object> g : games()) if (key(g).equals(key)) return g;
        return null;
    }

    String gameTitle(String key) {
        Map<String, Object> g = gameByKey(key);
        if (g != null) return (String) g.get("name");
        int c = key.indexOf(':');
        String s = c < 0 ? key : key.substring(c + 1).replace('-', ' ');
        return s.isEmpty() ? "a game" : Character.toUpperCase(s.charAt(0)) + s.substring(1);
    }

    /** Which of your games RetroArch is running, from its file name. */
    Map<String, Object> gameRunning(RetroCmd.Status s) {
        if (s == null || !s.hasGame()) return null;
        String clean = Library.cleanName(s.basename);
        String slug = Games.slug(clean);
        Map<String, Object> byName = null;
        for (Map<String, Object> g : games()) {
            String rom = (String) g.get("rom");
            int slash = rom.lastIndexOf('/');
            String file = slash >= 0 ? rom.substring(slash + 1) : rom;
            int dot = file.lastIndexOf('.');
            if (dot > 0) file = file.substring(0, dot);
            if (!rom.isEmpty() && file.equals(s.basename)) return g;
            if (byName == null && Games.slug((String) g.get("name")).equals(slug)) byName = g;
        }
        return byName;
    }

    // ================================================================ RetroArch
    String retroArchPackage() {
        for (String pkg : RETROARCH_PACKAGES) {
            try { app.getPackageManager().getPackageInfo(pkg, 0); return pkg; }
            catch (PackageManager.NameNotFoundException ignored) { }
        }
        return null;
    }

    /** Start the game in RetroArch. Returns "" or a reason it couldn't. */
    String boot(Context from, Map<String, Object> g) {
        Games.Sys s = sys(g);
        String rom = (String) g.get("rom");
        if (!s.hasNative()) return s.label + " games play by joining a friend's stream from their computer.";
        String pkg = retroArchPackage();
        if (pkg == null) return "RetroArch isn't installed. Open Settings > Run setup again to get it.";
        if (rom.isEmpty()) return "This game has no file set. Add its folder in Game folders.";
        try {
            Intent i = new Intent(Intent.ACTION_MAIN);
            i.setComponent(new ComponentName(pkg, "com.retroarch.browser.retroactivity.RetroActivityFuture"));
            i.putExtra("ROM", rom);
            i.putExtra("LIBRETRO", "/data/data/" + pkg + "/cores/" + s.core + "_libretro_android.so");
            i.putExtra("CONFIGFILE", "/storage/emulated/0/Android/data/" + pkg + "/files/retroarch.cfg");
            i.putExtra("QUITFOCUS", "");
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            from.startActivity(i);
        } catch (RuntimeException e) {
            return "Couldn't start RetroArch: " + e.getMessage();
        }
        hostingNow = false;
        playingKey = key(g);
        playingWith = "";
        prefs.edit().putString("last_boot", key(g)).apply();
        sendStatus();
        QueueService.start(app);
        changed();
        return "";
    }

    boolean canHostNative() {
        String s = prefs.getString("port_state", "");
        return prefs.getBoolean("can_host", false) || s.equals("open") || s.equals("mapped");
    }

    // ================================================================ the queue
    /** Tap on a game: start it, and queue unless you turned that off. */
    void play(Context from, Map<String, Object> g, boolean queueToo) {
        Games.Sys s = sys(g);
        if (s.hasNative()) {
            String problem = boot(from, g);
            if (!problem.isEmpty()) { say(problem, false); return; }
            if (queueToo) queue(g, true, null, "", "play", true);
            else say("Playing " + g.get("name") + ". Hold Start + Select for 3 seconds to look for an opponent.", false);
        } else {
            queue(g, false, null, "", "play", true);
        }
    }

    /** The controller shortcut: in the queue -> leave it; otherwise queue for the game that's running. */
    void hotkey() {
        if (hotkeyTest) {
            hotkeyTest = false;
            buzz(120);
            final Runnable r = onHotkeyTest;
            onHotkeyTest = null;
            main.post(() -> {
                if (r != null) r.run();
                for (Ui u : uiList()) u.say("The controller shortcut works.");
            });
            return;
        }
        work.execute(() -> {
            if (state == State.QUEUE) {
                leave();
                buzz(60);
                RetroCmd.showMsg("Retro-Netplay: you left the queue.");
                return;
            }
            RetroCmd.Status st = RetroCmd.status();
            Map<String, Object> g = gameRunning(st);
            boolean inGame = st != null && st.hasGame();
            if (g == null && !inGame) g = gameByKey(prefs.getString("last_boot", ""));
            if (g == null) {
                buzz(300);
                say(st == null ? "Couldn't ask RetroArch which game is running. Turn on Network Commands in RetroArch (setup shows how), or start the game from Retro-Netplay."
                        : "This game isn't in your game list yet. Add its folder in Retro-Netplay first.", st != null);
                return;
            }
            buzz(120);
            if (inGame) { playingKey = key(g); hostingNow = hostingNow && playingKey.equals(key(g)); }
            queue(g, inGame, null, "", "play", true);
        });
    }

    /**
     * Join the matchmaker queue for a game. inGame: you're already playing it (so you host if you can).
     * invite: a private room (a friend's call or an invite link), or null for the public queue.
     */
    void queue(Map<String, Object> g, boolean inGame, String invite, String with, String kind, boolean announce) {
        if (serverHost().isEmpty()) {
            say("Set the matchmaker address first (Settings).", inGame);
            return;
        }
        if (state == State.QUEUE) {
            if (queueKey.equals(key(g)) && invite == null) { say("You're already in the queue for " + g.get("name") + ".", inGame); return; }
            leave();
        }
        final Games.Sys s = sys(g);
        final String rom = (String) g.get("rom");
        final Plan.Offer offer = Plan.offer(s, retroArchPackage() != null, !rom.isEmpty(), canHostNative());
        final String host = serverHost();
        final int port = serverPort();
        final boolean friendsOnly = prefs.getBoolean("friends_only", false);
        final MatchClient mc = new MatchClient();
        search = mc;
        state = State.QUEUE;
        queueKey = key(g);
        queueName = (String) g.get("name");
        queueWith = with == null ? "" : with;
        queueSince = System.currentTimeMillis();
        sendStatus();
        if (announce) {
            String who = invite != null ? (queueWith.isEmpty() ? "your friend" : queueWith) : null;
            say(who != null ? "Connecting you with " + who + " for " + queueName + "..."
                    : "You're in the queue for " + queueName + "." + (inGame ? " Hold Start + Select for 3 seconds to leave." : ""), inGame);
        }
        QueueService.start(app);
        final String kindF = kind;
        new Thread(() -> {
            try {
                Map<String, Object> info = new LinkedHashMap<>();
                Map<String, Object> nat = new LinkedHashMap<>();
                if (inGame) {
                    String nick = RetroCmd.nickname();
                    if (!nick.isEmpty()) nat.put("nick", nick);
                }
                info.put("native", nat);
                info.put("stream", Plan.streamInfo(prefs.getBoolean("use_parsec", false)));
                boolean preferHost = inGame && Boolean.TRUE.equals(offer.canHost.get("native"));
                Map<String, Object> extra = Friends.searchFields(secret(), myName(), friends(), friendsOnly && invite == null, 15, preferHost);
                MatchClient.Match m = mc.search(host, port, queueKey, offer.lanes, info, offer.canHost, "android",
                        invite, invite != null, extra);
                if (search == mc) matched(g, m, host, port, inGame, kindF);
            } catch (MatchClient.Cancelled c) {
                // left the queue on purpose
            } catch (Exception e) {
                if (search == mc) {
                    search = null;
                    state = State.IDLE;
                    sendStatus();
                    say("Couldn't reach the matchmaker at " + host + ":" + port + " (" + e.getMessage() + "). Check Settings.", inGame);
                }
            }
        }, "queue").start();
    }

    void leave() {
        MatchClient mc = search;
        search = null;
        if (mc != null) mc.cancel();
        if (state == State.QUEUE) {
            state = State.IDLE;
            message = "You left the queue.";
        }
        sendStatus();
        changed();
    }

    private void matched(Map<String, Object> g, MatchClient.Match m, String host, int port, boolean inGame, String kind) {
        search = null;
        state = State.IDLE;
        Games.Sys s = sys(g);
        String who = m.peerString("name");
        if (who.isEmpty()) who = "an opponent";
        boolean friend = "true".equals(m.peerString("friend"));
        playingWith = who;
        String playUrl = Plan.playUrl(host, port, m.play, m.secureWeb);
        Plan plan = Plan.make(s, m.lane, m.role, m.peer, playUrl);
        buzz(400);
        if (plan.kind == Plan.Kind.ERROR) {
            playingWith = "";
            sendStatus();
            say(plan.message, inGame);
            return;
        }
        String hello = "Matched with " + who + (friend ? " (friend)" : "") + "!";
        if (plan.kind == Plan.Kind.OPEN_STREAM || plan.kind == Plan.Kind.OPEN_PARSEC) {
            playingKey = key(g);
            sendStatus();
            String url = plan.kind == Plan.Kind.OPEN_STREAM ? plan.clipboard : "parsec://peer_id=" + plan.clipboard;
            lastStreamUrl = url;
            Notify.matched(app, hello, "Tap to open the stream.", url);
            openUrl(url);
            say(hello + " Opening the stream.", false);
            return;
        }
        // native netplay in RetroArch
        playingKey = key(g);
        sendStatus();
        boolean ra = RetroCmd.available();
        if ("host".equals(m.role)) {
            if (ra && !hostingNow) { RetroCmd.hostToggle(); hostingNow = true; }
            String text = ra ? hello + " You're hosting. " + who + " is joining you."
                    : hello + " Open RetroArch's menu > Netplay > Host. " + who + " will connect to you.";
            if (!inGame && !ra) text = hello + " Start " + g.get("name") + " in RetroArch, then Netplay > Host.";
            Notify.matched(app, hello, text, "");
            say(text, ra);
        } else {
            String ip = m.peerString("ip"), nick = m.peerString("nick");
            if (!ip.isEmpty()) copy(ip);
            String find = nick.isEmpty() ? "" : "pick " + nick + " in Netplay > Refresh Netplay Host List, or ";
            String text = hello + " In RetroArch's menu: " + find + "Netplay > Connect to Netplay Host > " + ip + " (copied)."
                    + ("watch".equals(kind) ? " Then use Switch to watching." : "");
            Notify.matched(app, hello, text, "");
            say(text, ra);
        }
    }

    void watchToggle() { work.execute(RetroCmd::watchToggle); }

    private void copy(String text) {
        main.post(() -> {
            try {
                ClipboardManager cm = (ClipboardManager) app.getSystemService(Context.CLIPBOARD_SERVICE);
                cm.setPrimaryClip(ClipData.newPlainText("netplay", text));
            } catch (RuntimeException ignored) { }
        });
    }

    void openUrl(String url) {
        main.post(() -> {
            try {
                Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                app.startActivity(i);
            } catch (ActivityNotFoundException | SecurityException e) {
                say("Open this in a browser: " + url, false);
            }
        });
    }

    /** You're done playing: back to idle so friends see you as available. */
    void doneSession() {
        playingKey = "";
        playingWith = "";
        hostingNow = false;
        sendStatus();
        changed();
    }

    // ================================================================ calls
    /** Ask a friend to play (or to watch them). Their app may accept on its own if they gave you that privilege. */
    String callFriend(Friends.Friend f, String gameKey, String kind) {
        Presence p = presence;
        if (p == null || !presenceUp) return "You're not connected to the matchmaker. Check Settings.";
        if (gameByKey(gameKey) == null) return "You need " + gameTitle(gameKey) + " in your games first.";
        String id = p.call(f.id, gameKey, kind);
        if (id.isEmpty()) return "Couldn't reach the matchmaker.";
        outgoingCall = id;
        outgoingTo = f.id;
        say((kind.equals("watch") ? "Asking " + f.name + " if you can watch..." : "Asking " + f.name + " to play " + gameTitle(gameKey) + "..."), false);
        return "";
    }

    void cancelOutgoing() {
        Presence p = presence;
        if (p != null && !outgoingCall.isEmpty()) p.cancel(outgoingTo, outgoingCall);
        outgoingCall = "";
    }

    private void incoming(Call c) {
        Friends.Friend f = Friends.find(friends(), c.from);
        Presence p = presence;
        if (p == null) return;
        if (f == null) { p.answer(c.from, c.id, false); return; }
        boolean auto = ("play".equals(c.kind) && f.join) || ("watch".equals(c.kind) && f.watch);
        if (auto && (state == State.QUEUE ? queueKey.equals(c.game) : (playingKey.isEmpty() || playingKey.equals(c.game)))) {
            p.answer(c.from, c.id, true);
            say(f.name + ("watch".equals(c.kind) ? " is coming to watch." : " is joining you."), !playingKey.isEmpty());
            return;
        }
        synchronized (calls) { calls.add(c); }
        buzz(250);
        Notify.call(app, c, c.name + " " + c.what(this));
        RetroCmd.showMsg("Retro-Netplay: " + c.name + " " + c.what(this) + ". Answer on your phone's notification.");
        main.post(() -> { for (Ui u : uiList()) u.incomingCall(c); });
        changed();
    }

    void answer(String callId, boolean ok) {
        Call c = null;
        synchronized (calls) {
            for (Call x : calls) if (x.id.equals(callId)) c = x;
            if (c != null) calls.remove(c);
        }
        Notify.cancelCall(app, callId);
        Presence p = presence;
        if (c != null && p != null) p.answer(c.from, c.id, ok);
        changed();
    }

    private void started(String code, String game, String kind, String as, String withName) {
        Map<String, Object> g = gameByKey(game);
        if (g == null) {
            say("You don't have " + gameTitle(game) + " in your games, so " + withName + " can't join.", false);
            return;
        }
        outgoingCall = "";
        if ("callee".equals(as)) {
            boolean inGame = playingKey.equals(game);
            queue(g, inGame, code, withName, kind, true);
        } else {
            // you called: start the game first if you're going to play it, then meet them in the private room
            if (sys(g).hasNative() && !playingKey.equals(game)) {   // netplay needs the game running, even to watch
                String problem = boot(app, g);
                if (!problem.isEmpty()) { say(problem, false); return; }
            }
            queue(g, playingKey.equals(game), code, withName, kind, true);
        }
    }
}
