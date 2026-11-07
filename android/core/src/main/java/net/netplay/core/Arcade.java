package net.netplay.core;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.regex.Pattern;

/**
 * The phone's engine for the arcade (the same job as the desktop's engine.py, for a phone): stays connected to a
 * lobby, keeps what the interface shows, and turns what the player does into lobby messages and games. The
 * interface is the same web page as on the desktop (see LocalApi); what only a phone can do (open the player
 * window, open RetroArch, read its game folders) goes through {@link Platform}. A phone joins rooms; hosting from a
 * phone isn't there yet, so it says so.
 */
public final class Arcade {
    /** What the app does for the engine. */
    public interface Platform {
        Map<String, Object> loadSettings();
        void saveSettings(Map<String, Object> settings);
        /** The phone's games, as the interface lists them: i, name, system, key, problem, box, screens, ... */
        List<Map<String, Object>> library();
        void openPlayer(String host, int port, String key, String hostName, boolean useRelay);
        /** Start a netplay game against the host (native: the emulator's details). -> what to tell the player. */
        String openNetplay(Map<String, Object> game, Map<String, Object> nativeInfo);
        void stopGames();
        /** Matchmakers/lobbies found on this Wi-Fi, as "host:port". */
        List<String> findLobbies();
    }

    public interface Listener { void event(String name, Map<String, Object> data); }

    static final int CHAT_KEEP = 150;
    static final Pattern KEY = Pattern.compile("^[A-Za-z0-9_-]{8,64}$");
    static final Pattern CODE = Pattern.compile("[^A-Za-z0-9]");

    private final Platform pf;
    private final Map<String, Object> cfg;
    private final List<Listener> listeners = new CopyOnWriteArrayList<>();
    private Link link;
    private final Map<String, Object> lobby = new LinkedHashMap<>();
    private List<Object> rooms = new ArrayList<>();
    private Map<String, Object> seat;
    private List<Object> lobbyChat = new ArrayList<>(), roomChat = new ArrayList<>();
    private String note = "";
    private int version;

    public Arcade(Platform platform) {
        pf = platform;
        cfg = new LinkedHashMap<>(platform.loadSettings());
        if (!(cfg.get("secret") instanceof String) || !Friends.SECRET.matcher((String) cfg.get("secret")).matches())
            cfg.put("secret", Friends.newSecret());
        cfg.putIfAbsent("my_name", "");
        cfg.putIfAbsent("age_ok", false);
        cfg.putIfAbsent("chat_filter", true);
        cfg.putIfAbsent("sounds", true);
        cfg.putIfAbsent("use_relay", true);
        cfg.putIfAbsent("lobby", "");
        cfg.putIfAbsent("lobbies", new ArrayList<>());
        cfg.putIfAbsent("blocked", new ArrayList<>());
        pf.saveSettings(cfg);
        lobby.put("connected", false);
        lobby.put("address", "");
        lobby.put("name", "");
        lobby.put("rules", "");
        lobby.put("admin", false);
        lobby.put("online", 0);
        lobby.put("error", "");
        lobby.put("id", "");
    }

    public void listen(Listener l) { listeners.add(l); }
    public void unlisten(Listener l) { listeners.remove(l); }

    /** Connect to the saved lobby, else the first one on this Wi-Fi. */
    public void start() {
        String saved = str(cfg.get("lobby"));
        if (!saved.isEmpty()) { connect(saved); return; }
        new Thread(() -> {
            List<String> found = pf.findLobbies();
            if (!found.isEmpty()) connect(found.get(0));
            else synchronized (this) { lobby.put("error", "No lobby found on this Wi-Fi. Type a lobby's address in Settings."); changed(); }
        }, "find-lobby").start();
    }

    public void stop() { if (link != null) link.close(); }

    // ------------------------------------------------------------------ state for the interface
    public synchronized Map<String, Object> snapshot() {
        Map<String, Object> s = new LinkedHashMap<>();
        s.put("v", version);
        s.put("me", map("name", str(cfg.get("my_name")), "id", Friends.idOf(str(cfg.get("secret")))));
        boolean ageOk = Boolean.TRUE.equals(cfg.get("age_ok"));
        s.put("setup", map("done", ageOk && !str(cfg.get("my_name")).isEmpty(), "age_ok", ageOk));
        Map<String, Object> l = new LinkedHashMap<>(lobby);
        l.put("run_own", false);
        l.put("saved", cfg.get("lobbies"));
        l.put("share", null);
        s.put("lobby", l);
        s.put("rooms", rooms);
        s.put("myroom", null);
        s.put("requests", new ArrayList<>());
        s.put("seat", seat);
        s.put("chat", map("lobby", lobbyChat, "room", roomChat));
        s.put("library", pf.library());
        s.put("emulators", new LinkedHashMap<>());
        Map<String, Object> systems = new LinkedHashMap<>();
        for (Games.Sys sys : Games.SYSTEMS.values())      // (netplay: the emulator has its own, as on the desktop)
            systems.put(sys.id, map("label", sys.label, "netplay", "retroarch".equals(sys.emu) || "dolphin".equals(sys.emu)));
        s.put("systems", systems);
        s.put("settings", map("chat_filter", cfg.get("chat_filter"), "sounds", cfg.get("sounds"), "use_relay", cfg.get("use_relay"),
                              "game_folders", new ArrayList<>(), "seats", 2, "auto_accept", false, "prefer_netplay", true));
        s.put("blocked", cfg.get("blocked"));
        s.put("playing", map("note", note, "running", new ArrayList<>()));
        s.put("device", map("phone", true, "can_host", false));
        return s;
    }

    private void changed() {
        version++;
        Map<String, Object> snap = snapshot();
        for (Listener l : listeners) l.event("state", snap);
    }

    private void notice(String kind, String text, String sound) {
        Map<String, Object> n = map("kind", kind, "text", text);
        if (sound != null) n.put("sound", sound);
        for (Listener l : listeners) l.event("notice", n);
    }

    // ------------------------------------------------------------------ the lobby
    public synchronized void connect(String address) {
        String[] hp = parseLobby(address);
        if (hp == null) { notice("error", "That lobby address doesn't look right. It looks like 203.0.113.5:7777.", null); return; }
        if (link != null) link.close();
        lobby.put("connected", false);
        lobby.put("address", hp[0] + ":" + hp[1]);
        lobby.put("error", "");
        lobby.put("name", "");
        rooms = new ArrayList<>();
        seat = null;
        lobbyChat = new ArrayList<>();
        roomChat = new ArrayList<>();
        cfg.put("lobby", hp[0] + ":" + hp[1]);
        pf.saveSettings(cfg);
        link = new Link(hp[0], Integer.parseInt(hp[1]));
        link.start();
        changed();
    }

    /** "host", "host:port" or netplay://lobby?h=..&p=.. -> {host, port} or null. */
    public static String[] parseLobby(String text) {
        text = text == null ? "" : text.trim();
        if (text.startsWith("netplay://")) {
            String h = query(text, "h"), p = query(text, "p");
            text = h + ":" + (p.isEmpty() ? String.valueOf(Lan.DEFAULT_PORT) : p);
        }
        java.util.regex.Matcher m = Pattern.compile("^([A-Za-z0-9.-]+|\\[[0-9a-fA-F:]+\\])(?::(\\d{2,5}))?$").matcher(text);
        if (!m.matches() || !m.group(1).matches(".*[A-Za-z0-9].*")) return null;
        int port = m.group(2) == null ? Lan.DEFAULT_PORT : Integer.parseInt(m.group(2));
        if (port <= 0 || port > 65535) return null;
        return new String[]{m.group(1), String.valueOf(port)};
    }

    private static String query(String url, String key) {
        int q = url.indexOf('?');
        if (q < 0) return "";
        for (String part : url.substring(q + 1).split("&")) {
            int eq = part.indexOf('=');
            if (eq > 0 && part.substring(0, eq).equals(key)) {
                try { return java.net.URLDecoder.decode(part.substring(eq + 1), "UTF-8"); } catch (Exception e) { return ""; }
            }
        }
        return "";
    }

    private boolean send(Map<String, Object> msg) {
        if (link == null || !link.send(msg)) { notice("error", "You're not connected to a lobby right now.", null); return false; }
        return true;
    }

    private synchronized void lobbyDown(Link from, String why) {
        if (from != link) return;
        boolean was = Boolean.TRUE.equals(lobby.get("connected"));
        lobby.put("connected", false);
        if (why != null && !why.isEmpty()) lobby.put("error", why);
        rooms = new ArrayList<>();
        if (was) notice("warn", "Lost the connection to the lobby. Trying again...", null);
        changed();
    }

    @SuppressWarnings("unchecked")
    synchronized void onLobby(Link from, Map<String, Object> m) {
        if (from != link) return;                     // (an old connection's last words)
        String op = str(m.get("op"));
        switch (op) {
            case "welcome": {
                Map<String, Object> lb = asMap(m.get("lobby"));
                lobby.put("connected", true);
                lobby.put("error", "");
                lobby.put("name", str(lb.get("name")));
                lobby.put("rules", str(lb.get("rules")));
                lobby.put("admin", Boolean.TRUE.equals(m.get("admin")));
                lobby.put("online", m.get("online"));
                lobby.put("id", str(m.get("id")));
                rooms = asList(m.get("rooms"));
                lobbyChat = tail(asList(m.get("chat")));
                break;
            }
            case "rooms":
                rooms = asList(m.get("rooms"));
                lobby.put("online", m.get("online"));
                break;
            case "room": {
                Map<String, Object> r = asMap(m.get("room"));
                if (seat != null && str(r.get("id")).equals(str(asMap(seat.get("room")).get("id")))) seat.put("room", r);
                break;
            }
            case "waiting": {
                Map<String, Object> r = map("id", m.get("room"));
                for (Object o : rooms) if (str(asMap(o).get("id")).equals(str(m.get("room")))) r = asMap(o);
                seat = map("room", r, "seat", null, "waiting", true);
                break;
            }
            case "accepted":
                seat = map("room", asMap(m.get("room")), "seat", m.get("seat"), "waiting", false);
                roomChat = new ArrayList<>();
                notice("joined", "You're in! You're Player " + m.get("seat") + ".", "found");
                startGuest(m);
                break;
            case "native": {
                String code = CODE.matcher(str(asMap(m.get("native")).get("code"))).replaceAll("");
                if (seat != null && !code.isEmpty()) {
                    note = "The host shared Dolphin's code: " + code + ". In Dolphin: Tools > NetPlay > Connect.";
                    notice("info", note, null);
                }
                break;
            }
            case "declined": case "removed": case "room_closed":
                seat = null;
                roomChat = new ArrayList<>();
                note = "";
                if (!"declined".equals(op)) pf.stopGames();
                notice("warn", str(m.get("why")).isEmpty() ? "The room closed." : str(m.get("why")), null);
                break;
            case "chat": {
                if ("lobby".equals(str(m.get("to")))) lobbyChat = tail(add(lobbyChat, m)); else roomChat = tail(add(roomChat, m));
                break;
            }
            case "error_soft": case "error":
                notice("error", str(m.get("msg")).isEmpty() ? "Something went wrong." : str(m.get("msg")), null);
                if ("error".equals(op)) lobby.put("error", str(m.get("msg")));
                break;
            case "reported":
                notice("info", "Thanks. The person running this lobby will look at it.", null);
                break;
            case "muted": case "bye":
                notice("warn", m.get("why") != null ? str(m.get("why")) : "You're muted for " + m.get("minutes") + " minutes.", null);
                break;
            default:
                return;
        }
        changed();
    }

    private void startGuest(Map<String, Object> m) {
        Map<String, Object> room = asMap(m.get("room"));
        Map<String, Object> sess = asMap(m.get("session"));
        if (!sess.isEmpty()) {
            String key = str(sess.get("key"));
            if (!KEY.matcher(key).matches()) { notice("error", "The lobby sent a stream key that doesn't look right. Try joining again.", null); return; }
            String[] hp = parseLobby(str(cfg.get("lobby")));
            pf.openPlayer(hp[0], Integer.parseInt(hp[1]), key, str(asMap(room.get("host")).get("name")), !Boolean.FALSE.equals(cfg.get("use_relay")));
            note = str(asMap(room.get("host")).get("name")) + "'s game opens in the player. Your controller (or the on-screen buttons) is your player.";
            return;
        }
        Map<String, Object> game = null;
        for (Map<String, Object> g : pf.library()) if (str(g.get("key")).equals(str(room.get("game")))) game = g;
        if (game == null) {
            note = "This room uses netplay: you need your own copy of " + str(room.get("title")) + ". Add it to your games, then join again.";
            notice("warn", note, null);
            return;
        }
        note = pf.openNetplay(game, asMap(m.get("native")));
    }

    // ------------------------------------------------------------------ what the player did
    @SuppressWarnings("unchecked")
    public Map<String, Object> act(Map<String, Object> a) {
        String act = str(a.get("action"));
        synchronized (this) {
            switch (act) {
                case "setup": {
                    String name = Friends.cleanName(str(a.get("name")));
                    if (name.isEmpty()) return fail("Pick a name other players will see.");
                    if (!Boolean.TRUE.equals(a.get("age_ok"))) return fail("Retro-Netplay is for people 13 and older.");
                    cfg.put("my_name", name);
                    cfg.put("age_ok", true);
                    pf.saveSettings(cfg);
                    if (link != null) connect(str(cfg.get("lobby")));
                    break;
                }
                case "connect": {
                    connect(str(a.get("address")));
                    String addr = str(cfg.get("lobby"));
                    List<Object> saved = asList(cfg.get("lobbies"));
                    if (!addr.isEmpty() && !saved.contains(addr)) {
                        saved.add(0, addr);
                        cfg.put("lobbies", new ArrayList<>(saved.subList(0, Math.min(10, saved.size()))));
                        pf.saveSettings(cfg);
                    }
                    break;
                }
                case "join":
                    send(a.get("room") != null ? map("op", "join", "room", a.get("room")) : map("op", "join", "invite", a.get("invite")));
                    break;
                case "cancel_join":
                    send(map("op", "cancel_join"));
                    seat = null;
                    break;
                case "leave":
                    send(map("op", "leave"));
                    seat = null;
                    note = "";
                    pf.stopGames();
                    break;
                case "chat": {
                    Object to = "lobby".equals(a.get("to")) ? "lobby" : (seat == null ? null : asMap(seat.get("room")).get("id"));
                    String text = str(a.get("text"));
                    if (to != null) send(map("op", "chat", "to", to, "text", text.length() > 300 ? text.substring(0, 300) : text));
                    break;
                }
                case "report":
                    send(map("op", "report", "player", a.get("player"), "why", a.get("why"), "text", str(a.get("text"))));
                    break;
                case "block": case "unblock": {
                    String pid = str(a.get("player"));
                    List<Object> lst = new ArrayList<>();
                    for (Object b : asList(cfg.get("blocked"))) if (!pid.equals(str(asMap(b).get("id")))) lst.add(b);
                    if ("block".equals(act) && Friends.ID.matcher(pid).matches()) lst.add(map("id", pid, "name", Friends.cleanName(str(a.get("name")))));
                    cfg.put("blocked", lst);
                    pf.saveSettings(cfg);
                    List<Object> ids = new ArrayList<>();
                    for (Object b : lst) ids.add(asMap(b).get("id"));
                    if (link != null) link.send(map("op", "block", "ids", ids));
                    break;
                }
                case "settings":
                    for (String k : new String[]{"chat_filter", "sounds", "use_relay"})
                        if (a.get(k) instanceof Boolean) cfg.put(k, a.get(k));
                    pf.saveSettings(cfg);
                    break;
                case "post": case "update_room": case "close_room": case "answer": case "kick":
                    return fail("Hosting from a phone is coming later. Host from a computer, or join a room here.");
                case "mod":
                    send(map("op", "mod", "action", a.get("what"), "player", a.get("player"), "minutes", a.get("minutes")));
                    break;
                default:
                    return fail("unknown action");
            }
            changed();
        }
        return map("ok", true);
    }

    // ------------------------------------------------------------------ the connection
    private final class Link extends Thread {
        final String host;
        final int port;
        volatile boolean stopped;
        private Socket sock;

        Link(String host, int port) { super("lobby-link"); setDaemon(true); this.host = host; this.port = port; }

        synchronized boolean send(Map<String, Object> msg) {
            if (sock == null) return false;
            try {
                OutputStream o = sock.getOutputStream();
                o.write((Json.stringify(msg) + "\n").getBytes(StandardCharsets.UTF_8));
                o.flush();
                return true;
            } catch (IOException e) { return false; }
        }

        void close() {
            stopped = true;
            synchronized (this) { try { if (sock != null) sock.close(); } catch (IOException ignored) { } }
        }

        @Override public void run() {
            long wait = 1000;
            while (!stopped) {
                try {
                    Socket s = MmTls.connect(host, port, 8000);
                    if (stopped) { s.close(); return; }      // replaced while it was connecting
                    s.setSoTimeout(0);
                    List<Object> blocked = new ArrayList<>();
                    for (Object b : asList(cfg.get("blocked"))) blocked.add(asMap(b).get("id"));
                    String name = str(cfg.get("my_name"));
                    Map<String, Object> hello = map("op", "lobby", "v", 2, "me", cfg.get("secret"), "name", name.isEmpty() ? "Player" : name,
                                                    "client", "android", "blocked", blocked);
                    s.getOutputStream().write((Json.stringify(hello) + "\n").getBytes(StandardCharsets.UTF_8));
                    synchronized (this) { sock = s; }
                    wait = 1000;
                    BufferedReader r = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
                    String line;
                    while ((line = r.readLine()) != null) {
                        Map<String, Object> m;
                        try { m = Json.parseObject(line); } catch (RuntimeException e) { continue; }
                        if (stopped) break;
                        if ("ping".equals(m.get("op"))) send(map("op", "pong"));
                        else onLobby(this, m);
                    }
                } catch (IOException | RuntimeException e) {
                    if (!stopped) lobbyDown(this, e.getMessage() == null ? "" : e.getMessage());
                }
                synchronized (this) { sock = null; }
                if (stopped) return;
                lobbyDown(this, "");
                try { Thread.sleep(wait); } catch (InterruptedException e) { return; }
                wait = Math.min(wait * 2, 30000);
            }
        }
    }

    // ------------------------------------------------------------------ small helpers
    static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    static Map<String, Object> fail(String msg) { return map("ok", false, "msg", msg); }

    static String str(Object o) { return o == null ? "" : String.valueOf(o); }

    @SuppressWarnings("unchecked")
    static Map<String, Object> asMap(Object o) { return o instanceof Map ? (Map<String, Object>) o : new LinkedHashMap<>(); }

    @SuppressWarnings("unchecked")
    static List<Object> asList(Object o) { return o instanceof List ? new ArrayList<>((List<Object>) o) : new ArrayList<>(); }

    static List<Object> add(List<Object> l, Object o) { List<Object> n = new ArrayList<>(l); n.add(o); return n; }

    static List<Object> tail(List<Object> l) { return l.size() <= CHAT_KEEP ? l : new ArrayList<>(l.subList(l.size() - CHAT_KEEP, l.size())); }
}
