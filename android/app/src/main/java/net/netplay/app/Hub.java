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
import net.netplay.core.MmTls;
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

    /** A friend asking to join you. */
    static final class Call {
        final String from, name, game, id;
        Call(String from, String name, String game, String id) {
            this.from = from; this.name = name; this.game = game; this.id = id;
        }
        String what(Hub h) { return "wants to join you in " + h.gameTitle(game); }
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
    /** Why the friends connection is down, in plain words ("" while connecting or connected). */
    volatile String presenceProblem = "";
    /** Accepts made while not connected (e.g. from a notification button), sent once connected. */
    private final java.util.Set<String> pendingAccepts = java.util.Collections.synchronizedSet(new java.util.LinkedHashSet<>());
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
        // matchmakers seen with encryption stay encrypted (a later plain-text answer is refused), across restarts
        MmTls.seen = new MmTls.Seen() {
            @Override public synchronized boolean has(String host) {
                return prefs.getStringSet("tls_seen", java.util.Collections.emptySet()).contains(host);
            }
            @Override public synchronized void add(String host) {
                java.util.Set<String> all = new java.util.HashSet<>(prefs.getStringSet("tls_seen", java.util.Collections.emptySet()));
                if (all.add(host)) prefs.edit().putStringSet("tls_seen", all).apply();
            }
        };
    }

    // ================================================================ listeners
    void addUi(Ui u) {
        synchronized (uis) { if (!uis.contains(u)) uis.add(u); }
        main.removeCallbacks(stopWhenUnused);
        main.removeCallbacks(keepFinding);
        main.postDelayed(keepFinding, 30000);
    }

    /** When the last screen closes (and you're not queued or playing), stop being online a moment later. Waiting a
     *  moment matters: going from one screen to another closes the first one after the second opens. */
    void removeUi(Ui u) {
        boolean none;
        synchronized (uis) { uis.remove(u); none = uis.isEmpty(); }
        if (none) { main.removeCallbacks(stopWhenUnused); main.postDelayed(stopWhenUnused, 3000); }
    }

    private final Runnable stopWhenUnused = () -> {
        if (uiList().isEmpty() && state == State.IDLE && playingKey.isEmpty()) stopPresence();
    };

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
        if (p == null || !presenceUp) return serverHost().isEmpty() ? "Looking for Retro-Netplay on your Wi-Fi. Open it on your computer too." : "Still connecting to the matchmaker. Try again in a moment.";
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
        friendsChanged();
        Presence p = presence;
        if (p != null) { if (theyAsked) p.accept(id); else p.request(id); }
        changed();
        return theyAsked ? "You and " + f.name + " are friends now." : "Friend request sent to " + f.name + ". You'll be friends when they accept.";
    }

    static String plainProblem(String why) {
        String w = why == null ? "" : why.toLowerCase(java.util.Locale.ROOT);
        if (w.isEmpty()) return "";
        if (w.contains("refused")) return "The computer answered, but nothing is listening: the matchmaker isn't running there.";
        if (w.contains("timed out") || w.contains("timeout")) return "No answer from that address (wrong address, different Wi-Fi, or a firewall).";
        if (w.contains("unreachable") || w.contains("no route")) return "That address can't be reached from this network.";
        if (w.contains("unknown host") || w.contains("unable to resolve")) return "That address doesn't exist.";
        return "Connection problem (" + why + ").";
    }

    void answerRequest(String id, boolean ok) {
        Notify.cancelRequest(app, id);
        HotkeyService.closeCard("req:" + id);
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
            friendsChanged();
            Presence p = presence;
            if (p != null && presenceUp) p.accept(id);
            else pendingAccepts.add(id);         // sent as soon as the connection is up
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
            friendsChanged();
            Presence p = presence;
            if (p != null) p.accept(id);
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
        Notify.request(app, id, name + " wants to be friends", (code.isEmpty() ? "" : "Code " + code + ". ") + "Accept to see each other online and join each other's games.");
        HotkeyService.showCard(app, "Friend request", name + (code.isEmpty() ? "" : " (" + code + ")") + " wants to be friends.",
                () -> answerRequest(id, true), () -> { }, "req:" + id);
        say(name + " wants to be friends. Open Friends to accept.", false);
        changed();
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
        friendsChanged();
        return true;
    }

    void removeFriend(String id) {
        List<Friends.Friend> l = friends();
        Friends.Friend f = Friends.find(l, id);
        if (f != null) { l.remove(f); saveFriends(l); friendsChanged(); }
    }

    /** Tell the matchmaker your new friend list on the open connection (no reconnect, so nothing in flight is lost). */
    private void friendsChanged() {
        Presence p = presence;
        if (p != null) p.setFriends(Friends.ids(friends()));
    }

    void updateFriend(Friends.Friend f) {
        List<Friends.Friend> l = friends();
        for (int i = 0; i < l.size(); i++) if (l.get(i).id.equals(f.id)) l.set(i, f);
        saveFriends(l);
    }

    /** The matchmaker's address. 127.0.0.1 / localhost count as "not set": on a phone they'd mean the phone itself. */
    String serverHost() {
        String h = prefs.getString("server_host", "").trim();
        return net.netplay.core.Lan.isLoopback(h) ? "" : h;
    }

    interface FoundCallback { void found(List<net.netplay.core.Lan.Found> list); }

    /** Where to send the "is there a matchmaker here?" broadcast on this Wi-Fi network. */
    List<String> broadcastTargets() {
        List<String> t = new ArrayList<>();
        t.add("255.255.255.255");
        try {
            android.net.wifi.WifiManager w = (android.net.wifi.WifiManager) app.getSystemService(Context.WIFI_SERVICE);
            @SuppressWarnings("deprecation") android.net.DhcpInfo d = w.getDhcpInfo();
            if (d != null && d.ipAddress != 0) {
                int mask = d.netmask != 0 ? d.netmask : 0x00FFFFFF;
                int b = (d.ipAddress & mask) | ~mask;
                t.add((b & 0xFF) + "." + ((b >> 8) & 0xFF) + "." + ((b >> 16) & 0xFF) + "." + ((b >> 24) & 0xFF));
            }
        } catch (RuntimeException ignored) { }
        return t;
    }

    /** This phone's address on the Wi-Fi, or "" (used to knock on the other addresses when broadcasts are dropped). */
    String myWifiIp() {
        try {
            android.net.wifi.WifiManager w = (android.net.wifi.WifiManager) app.getSystemService(Context.WIFI_SERVICE);
            @SuppressWarnings("deprecation") int ip = w.getConnectionInfo().getIpAddress();
            if (ip == 0) return "";
            return (ip & 0xFF) + "." + ((ip >> 8) & 0xFF) + "." + ((ip >> 16) & 0xFF) + "." + ((ip >> 24) & 0xFF);
        } catch (RuntimeException e) {
            return "";
        }
    }

    /** Look for matchmakers on the home network; the answer comes back on the main thread. */
    void findOnNetwork(FoundCallback cb) {
        final List<String> targets = broadcastTargets();
        final String me = myWifiIp();
        new Thread(() -> {
            List<net.netplay.core.Lan.Found> res = net.netplay.core.Lan.findAll(targets, me, net.netplay.core.Lan.DEFAULT_PORT);
            main.post(() -> cb.found(res));
        }, "discover").start();
    }

    /** A matchmaker chosen by hand. A home-network address keeps finding itself again if it moves; an internet one stays. */
    void useMatchmaker(String host, int port) {
        prefs.edit().putString("server_host", host).putInt("server_port", port)
                .putBoolean("auto_mm", net.netplay.core.Lan.isPrivate(host)).putString("server_name", "").apply();
        restartPresence();
        changed();
    }

    /** Looking for the matchmaker on the Wi-Fi right now. */
    volatile boolean finding;
    /** The last look found no Retro-Netplay on this Wi-Fi. */
    volatile boolean nothingFound;
    private long foundAt;

    String serverName() {
        String n = prefs.getString("server_name", "");
        return n.isEmpty() ? serverHost() : n;
    }

    /** Pick the matchmaker by itself unless an internet address was typed on purpose. */
    boolean autoOn() {
        String h = serverHost();
        return prefs.getBoolean("auto_mm", true) && (h.isEmpty() || net.netplay.core.Lan.isPrivate(h));
    }

    void autoFind() { autoFind(false); }

    /** Find this home network's matchmaker and use it: when the app opens, and whenever the connection drops.
     *  Every device picks the same one by the same rule (Lan.pick), so nobody types or passes on an address. */
    void autoFind(boolean force) {
        if (!autoOn() || finding) return;
        long now = android.os.SystemClock.elapsedRealtime();
        if (!force && foundAt != 0 && now - foundAt < 20000) return;
        foundAt = now;
        finding = true;
        changed();
        findOnNetwork(list -> {
            finding = false;
            net.netplay.core.Lan.Found f = net.netplay.core.Lan.pick(list);
            nothingFound = f == null;
            if (f != null) {
                boolean moved = !f.host.equals(serverHost()) || f.port != serverPort();
                prefs.edit().putString("server_host", f.host).putInt("server_port", f.port)
                        .putString("server_name", f.name).apply();
                if (moved || !presenceUp) restartPresence();
            }
            changed();
        });
    }

    /** While a screen is open and the connection is down, look again every 30 seconds. */
    private final Runnable keepFinding = new Runnable() {
        @Override public void run() {
            if (uiList().isEmpty()) return;
            if (!presenceUp) autoFind(false);
            main.postDelayed(this, 30000);
        }
    };
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
                buzz(150);
                Notify.info(app, f.name + " accepted your friend request", "You're friends now. When they're online, tap Join next to their name.");
                changed();
            }
            @Override public void onSoftError(String msg) { say("Friends: " + msg, false); }
            @Override public void onRequestSent(String to) {
                Friends.Friend f = Friends.find(friends(), to);
                say("Friend request sent" + (f == null ? "" : " to " + f.name) + ". They'll get a notification to accept.", false);
            }
            @Override public void onConnected(boolean c, String why) {
                presenceUp = c;
                presenceProblem = c ? "" : plainProblem(why);
                if (c) {
                    Presence pp = presence;
                    List<String> todo;
                    synchronized (pendingAccepts) { todo = new ArrayList<>(pendingAccepts); pendingAccepts.clear(); }
                    if (pp != null) for (String id : todo) pp.accept(id);
                }
                if (!c) {
                    synchronized (online) { online.clear(); }
                    main.post(() -> autoFind(false));   // the matchmaker went away or moved: look for it again
                }
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
            @Override public void onCall(String from, String name, String game, String id) { incoming(new Call(from, name, game, id)); }
            @Override public void onStart(String id, String code, String game, String as, String withId, String withName) {
                started(code, game, as, withName);
            }
            @Override public void onDeclined(String id, String name, String why) {
                outgoingCall = "";
                say(!why.isEmpty() ? why : (name.isEmpty() ? "Your friend" : name) + " said no this time.", false);
            }
            @Override public void onCallFailed(String id, String msg) {
                outgoingCall = "";
                say(msg.equals("not online") ? "That friend isn't online right now." : "The call didn't go through: " + msg, false);
            }
            @Override public void onCallCancelled(String id, String from) {
                synchronized (calls) { calls.removeIf(c -> c.id.equals(id)); }
                Notify.cancelCall(app, id);
                HotkeyService.closeCard(id);
                changed();
            }
        });
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
        if (state == State.QUEUE) p.status("queue", queueKey, abilities(queueKey));
        else if (!playingKey.isEmpty()) p.status("playing", playingKey, abilities(playingKey));
        else p.status("idle", "");
    }

    Map<String, Object> onlineInfo(String id) { synchronized (online) { return online.get(id); } }

    /** What this phone can host for a game: emulator netplay if RetroArch, the file and the router allow; never a stream. */
    Map<String, Object> abilities(String key) {
        Map<String, Object> m = new LinkedHashMap<>();
        Map<String, Object> g = gameByKey(key);
        boolean nat = g != null && sys(g).hasNative() && retroArchPackage() != null && !((String) g.get("rom")).isEmpty() && canHostNative();
        m.put("native", nat);
        m.put("stream", false);
        return m;
    }

    /** "" if joining a friend hosting gameKey (with abilities can) should work from this phone, else why not. */
    String joinProblem(Map<String, Object> can, String gameKey, String friendName) {
        if (can == null || can.isEmpty()) return "";                       // an older app that doesn't say: just try
        Games.Sys s = Games.SYSTEMS.get(gameKey.contains(":") ? gameKey.substring(0, gameKey.indexOf(':')) : "");
        if (s == null) return "That game's system isn't supported here.";
        if (Boolean.TRUE.equals(can.get("stream"))) return "";               // the phone joins the stream
        Map<String, Object> g = gameByKey(gameKey);
        boolean iCan = g != null && s.hasNative() && retroArchPackage() != null && !((String) g.get("rom")).isEmpty();
        if (Boolean.TRUE.equals(can.get("native")) && iCan) return "";
        if (!s.hasNative()) return friendName + "'s computer can't stream " + gameTitle(gameKey) + " yet. On their computer: Settings > "
                + "Streaming check shows what to install.";
        if (!iCan) return "To join " + gameTitle(gameKey) + " you need RetroArch and the game on this phone, or " + friendName
                + "'s computer needs the streaming tools (Settings > Streaming check).";
        return friendName + " can't host right now (their router port is closed and they can't stream). Ask them to try "
                + "joining you instead.";
    }

    /** A game you don't have, to join a friend's stream of it (nothing to run on this phone). */
    Map<String, Object> streamOnly(String key) {
        String sysId = key.contains(":") ? key.substring(0, key.indexOf(':')) : "";
        if (!Games.SYSTEMS.containsKey(sysId)) return null;
        Map<String, Object> g = new LinkedHashMap<>();
        g.put("name", gameTitle(key));
        g.put("system", sysId);
        g.put("rom", "");
        return g;
    }

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
            if (queueToo) queue(g, true, null, "", "", true);
            else say("Playing " + g.get("name") + ". Hold Start + Select for 3 seconds to look for an opponent.", false);
        } else {
            queue(g, false, null, "", "", true);
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
            queue(g, inGame, null, "", "", true);
        });
    }

    /**
     * Join the matchmaker queue for a game. inGame: you're already playing it (so you host if you can).
     * invite: a private room (a friend's join or an invite link), or null for the public queue.
     * as: in a friend's join, "callee" (you allowed it: you host) or "caller" (you asked: you connect); else "".
     */
    void queue(Map<String, Object> g, boolean inGame, String invite, String with, String as, boolean announce) {
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
                boolean preferHost = "callee".equals(as) || (!"caller".equals(as) && inGame && Boolean.TRUE.equals(offer.canHost.get("native")));
                Map<String, Object> extra = Friends.searchFields(secret(), myName(), preferHost);
                MatchClient.Match m = mc.search(host, port, queueKey, offer.lanes, info, offer.canHost, "android",
                        invite, invite != null, extra);
                if (search == mc) matched(g, m, host, port, inGame);
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

    private void matched(Map<String, Object> g, MatchClient.Match m, String host, int port, boolean inGame) {
        search = null;
        state = State.IDLE;
        Games.Sys s = sys(g);
        String who = m.peerString("name");
        if (who.isEmpty()) who = "an opponent";
        boolean friend = "true".equals(m.peerString("friend"));
        playingWith = who;
        Map<String, Object> peer = new LinkedHashMap<>(m.peer);
        // the host is on the matchmaker's own network: reach it at the matchmaker's address
        if ("true".equals(m.peerString("at_matchmaker")) && !net.netplay.core.Lan.isLoopback(host)) peer.put("ip", host);
        Plan plan = Plan.make(s, m.lane, m.role, peer, "tcp".equals(m.sig));
        buzz(400);
        if (plan.kind == Plan.Kind.ERROR) {
            playingWith = "";
            sendStatus();
            say(plan.message, inGame);
            return;
        }
        String hello = "Matched with " + who + (friend ? " (friend)" : "") + "!";
        if (plan.kind == Plan.Kind.OPEN_PLAYER) {      // the app's own player: no browser, no web page
            playingKey = key(g);
            sendStatus();
            String link = PlayerActivity.link(host, port, m.sessionKey, String.valueOf(g.get("name")), prefs.getBoolean("use_relay", true));
            lastStreamUrl = link;
            Notify.matched(app, hello, "Tap to open the stream.", link);
            PlayerActivity.open(app, link);
            say(hello + " Opening the stream.", false);
            return;
        }
        if (plan.kind == Plan.Kind.OPEN_PARSEC) {
            playingKey = key(g);
            sendStatus();
            String url = "parsec://peer_id=" + plan.clipboard;
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
            String ip = plan.clipboard, nick = m.peerString("nick");
            if (!ip.isEmpty()) copy(ip);
            String find = nick.isEmpty() ? "" : "pick " + nick + " in Netplay > Refresh Netplay Host List, or ";
            String text = hello + " In RetroArch's menu: " + find + "Netplay > Connect to Netplay Host > " + ip + " (copied).";
            Notify.matched(app, hello, text, "");
            say(text, ra);
        }
    }

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

    // ================================================================ joining a friend
    /** Ask a friend to let you into their game. They tap Allow; then they host and this phone connects to them. */
    String callFriend(Friends.Friend f, String gameKey) {
        Presence p = presence;
        if (p == null || !presenceUp) return "You're not connected to the matchmaker. See the top of the Friends screen.";
        if (f.pending) return f.name + " hasn't accepted your friend request yet.";
        Map<String, Object> on = onlineInfo(f.id);
        if (on == null) return f.name + " isn't online right now.";
        @SuppressWarnings("unchecked")
        Map<String, Object> can = on.get("can") instanceof Map ? (Map<String, Object>) on.get("can") : null;
        boolean theirGame = gameKey.equals(on.get("game"));
        if (theirGame) {
            String problem = joinProblem(can, gameKey, f.name);
            if (!problem.isEmpty()) return problem;
        }
        boolean viaStream = theirGame && can != null && Boolean.TRUE.equals(can.get("stream"));
        if (gameByKey(gameKey) == null && !viaStream) return "You need " + gameTitle(gameKey) + " in your games first (same name).";
        String id = p.call(f.id, gameKey);
        if (id.isEmpty()) return "Couldn't reach the matchmaker.";
        outgoingCall = id;
        outgoingTo = f.id;
        say("Asking " + f.name + " to let you into " + gameTitle(gameKey) + "... They'll see Allow / Deny.", false);
        return "";
    }

    void cancelOutgoing() {
        Presence p = presence;
        if (p != null && !outgoingCall.isEmpty()) p.cancel(outgoingTo, outgoingCall);
        outgoingCall = "";
    }

    /** A friend wants to join: always ask (Allow / Deny), unless this phone can't play that game at all. */
    private void incoming(Call c) {
        Friends.Friend f = Friends.find(friends(), c.from);
        Presence p = presence;
        if (p == null) return;
        if (f == null) { p.answer(c.from, c.id, false); return; }
        if (gameByKey(c.game) == null) {
            p.answer(c.from, c.id, false, myName() + " doesn't have " + gameTitle(c.game) + " in their games.");
            say(c.name + " wanted to join you in " + gameTitle(c.game) + ", but it isn't in your games.", false);
            return;
        }
        synchronized (calls) { calls.add(c); }
        buzz(250);
        Notify.call(app, c, c.name + " " + c.what(this));
        HotkeyService.showCard(app, "Join request", c.name + " " + c.what(this),
                () -> answer(c.id, true), () -> answer(c.id, false), c.id);
        work.execute(() -> RetroCmd.showMsg("Retro-Netplay: " + c.name + " " + c.what(this) + ". Answer on your phone's notification."));
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
        HotkeyService.closeCard(callId);
        Presence p = presence;
        if (c != null && p != null) p.answer(c.from, c.id, ok);
        changed();
    }

    private void started(String code, String game, String as, String withName) {
        Map<String, Object> g = gameByKey(game);
        if (g == null && "caller".equals(as)) g = streamOnly(game);    // joining their stream: nothing to run here
        if (g == null) {
            say("You don't have " + gameTitle(game) + " in your games, so " + withName + " can't join.", false);
            return;
        }
        outgoingCall = "";
        if ("callee".equals(as)) {
            boolean inGame = playingKey.equals(game);
            queue(g, inGame, code, withName, "callee", true);
        } else {
            // you called: start the game first if you're going to play it, then meet them in the private room
            if (sys(g).hasNative() && !((String) g.get("rom")).isEmpty() && !playingKey.equals(game)) {   // netplay needs the game running
                String problem = boot(app, g);
                if (!problem.isEmpty()) { say(problem, false); return; }
            }
            queue(g, playingKey.equals(game), code, withName, "caller", true);
        }
    }
}
