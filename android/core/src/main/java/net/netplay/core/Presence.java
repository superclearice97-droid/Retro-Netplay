package net.netplay.core;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The "hello" connection: keeps you visible to your (mutual) friends and carries calls between you.
 * Reconnects by itself. All callbacks run on this class's own thread; hop to the UI thread yourself.
 */
public final class Presence {
    public interface Listener {
        void onConnected(boolean connected, String problem);
        void onOnline(List<Map<String, Object>> online);
        void onCall(String fromId, String name, String game, String kind, String callId);
        /** The call was accepted (by you or them). as = "caller" or "callee"; withId/withName = the friend. */
        void onStart(String callId, String code, String game, String kind, String as, String withId, String withName);
        void onDeclined(String callId, String name);
        void onCallFailed(String callId, String msg);
        void onCallCancelled(String callId, String fromId);
        /** Your own friend code on this matchmaker (K7M-4QZ). */
        default void onMyCode(String code) { }
        /** Answer to lookup(): found = false if no one has that code. */
        default void onLookup(String code, boolean found, String id, String name, boolean self) { }
        /** Someone sent you a friend request. */
        default void onFriendRequest(String fromId, String name, String code) { }
        /** Someone accepted your request. */
        default void onFriendAccepted(String fromId, String name, String code) { }
        default void onSoftError(String msg) { }
        /** The matchmaker took your friend request (it's delivered now, or when they next connect). */
        default void onRequestSent(String toId) { }
    }

    private static final SecureRandom RNG = new SecureRandom();
    private static final String CALL_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789";

    private final String host, secret, name;
    private final int port;
    private final List<String> friends;
    private final boolean hidden;
    private final Listener listener;
    private volatile boolean stopped;
    private volatile Socket socket;
    private volatile String state = "idle", game = "";
    private Thread thread;

    public Presence(String host, int port, String secret, String name, List<String> friendIds, Listener l) {
        this(host, port, secret, name, friendIds, l, false);
    }

    /** hidden: friends don't see you online, but requests and calls still reach you. */
    public Presence(String host, int port, String secret, String name, List<String> friendIds, Listener l, boolean hidden) {
        this.host = host; this.port = port; this.secret = secret; this.name = name;
        this.friends = new ArrayList<>(friendIds); this.listener = l; this.hidden = hidden;
    }

    public synchronized void start() {
        if (thread != null) return;
        thread = new Thread(this::run, "presence");
        thread.setDaemon(true);
        thread.start();
    }

    public void stop() {
        stopped = true;
        Socket s = socket;
        if (s != null) try { s.close(); } catch (IOException ignored) { }
    }

    public boolean isConnected() { Socket s = socket; return s != null && s.isConnected() && !s.isClosed(); }

    private void run() {
        long backoff = 2000;
        while (!stopped) {
            try {
                session();
                backoff = 2000;
            } catch (IOException | RuntimeException e) {
                if (stopped) break;
                listener.onConnected(false, e.getMessage() == null ? "connection lost" : e.getMessage());
            }
            if (stopped) break;
            try { Thread.sleep(backoff); } catch (InterruptedException e) { break; }
            backoff = Math.min(60000, backoff * 2);
        }
        listener.onConnected(false, "");
    }

    private void session() throws IOException {
        Socket s = new Socket();
        socket = s;
        try {
            s.connect(new InetSocketAddress(host, port), 8000);
            s.setKeepAlive(true);
            s.setSoTimeout(30000);
            Map<String, Object> hello = new LinkedHashMap<>();
            hello.put("v", 1L);
            hello.put("op", "hello");
            hello.put("me", secret);
            hello.put("name", name);
            hello.put("friends", friends);
            hello.put("hidden", hidden);
            write(hello);
            if (!"idle".equals(state) || !game.isEmpty()) status(state, game, can);
            BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
            boolean said = false;
            while (!stopped) {
                String line;
                try {
                    line = in.readLine();
                } catch (java.net.SocketTimeoutException t) {
                    ping();
                    continue;
                }
                if (line == null) throw new IOException("the matchmaker hung up");
                Map<String, Object> m;
                try { m = Json.parseObject(line); } catch (RuntimeException e) { continue; }
                String op = str(m.get("op"));
                switch (op) {
                    case "ping": ping(); break;
                    case "error": throw new IOException(str(m.get("msg")));
                    case "hello":
                        listener.onMyCode(Friends.pretty(str(m.get("code"))));
                        if (!said) { said = true; listener.onConnected(true, ""); }
                        break;
                    case "lookup":
                        listener.onLookup(Friends.pretty(str(m.get("code"))), Boolean.TRUE.equals(m.get("found")) && Friends.ID.matcher(str(m.get("id"))).matches(),
                                str(m.get("id")), Friends.cleanName(str(m.get("name"))), Boolean.TRUE.equals(m.get("self")));
                        break;
                    case "friend_request":
                        if (Friends.ID.matcher(str(m.get("from"))).matches())
                            listener.onFriendRequest(str(m.get("from")), Friends.cleanName(str(m.get("name"))), Friends.pretty(str(m.get("code"))));
                        break;
                    case "friend_accept":
                        if (Friends.ID.matcher(str(m.get("from"))).matches())
                            listener.onFriendAccepted(str(m.get("from")), Friends.cleanName(str(m.get("name"))), Friends.pretty(str(m.get("code"))));
                        break;
                    case "error_soft": listener.onSoftError(str(m.get("msg"))); break;
                    case "request_sent": listener.onRequestSent(str(m.get("to"))); break;
                    case "friends": listener.onOnline(listOfMaps(m.get("online"))); break;
                    case "call":
                        if (Friends.ID.matcher(str(m.get("from"))).matches())
                            listener.onCall(str(m.get("from")), Friends.cleanName(str(m.get("name"))), str(m.get("game")), str(m.get("kind")), str(m.get("call")));
                        break;
                    case "start": {
                        Map<?, ?> w = m.get("with") instanceof Map ? (Map<?, ?>) m.get("with") : new LinkedHashMap<>();
                        listener.onStart(str(m.get("call")), str(m.get("code")), str(m.get("game")), str(m.get("kind")), str(m.get("as")),
                                str(w.get("id")), Friends.cleanName(str(w.get("name"))));
                        break;
                    }
                    case "declined": listener.onDeclined(str(m.get("call")), Friends.cleanName(str(m.get("name")))); break;
                    case "call_failed": listener.onCallFailed(str(m.get("call")), str(m.get("msg"))); break;
                    case "call_cancelled": listener.onCallCancelled(str(m.get("call")), str(m.get("from"))); break;
                    default: break;
                }
            }
        } finally {
            socket = null;
            try { s.close(); } catch (IOException ignored) { }
        }
    }

    private static String str(Object o) { return o == null ? "" : String.valueOf(o); }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listOfMaps(Object o) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (o instanceof List) for (Object x : (List<Object>) o) if (x instanceof Map) out.add((Map<String, Object>) x);
        return out;
    }

    private void write(Map<String, Object> m) throws IOException {
        Socket s = socket;
        if (s == null) throw new IOException("not connected");
        synchronized (this) {
            OutputStream out = s.getOutputStream();
            out.write((Json.stringify(m) + "\n").getBytes(StandardCharsets.UTF_8));
            out.flush();
        }
    }

    private boolean quiet(Map<String, Object> m) {
        try { write(m); return true; } catch (IOException | RuntimeException e) { return false; }
    }

    private void ping() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("op", "ping");
        quiet(m);
    }

    /** state: idle, queue or playing. game: a game key or "". Remembered and re-sent after a reconnect. */
    public void status(String state, String game) { status(state, game, null); }

    /** can: what you can host right now ({"native": bool, "stream": bool}), so friends can tell if joining will work. */
    public void status(String state, String game, Map<String, Object> can) {
        this.state = state;
        this.game = game == null ? "" : game;
        this.can = can;
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("op", "status");
        m.put("state", state);
        m.put("game", this.game);
        if (can != null) m.put("can", can);
        quiet(m);
    }

    private volatile Map<String, Object> can;

    /** Ask a friend to play (kind "play") or to watch them ("watch"). Returns the call id, or "" if not connected. */
    public String call(String friendId, String gameKey, String kind) {
        StringBuilder id = new StringBuilder();
        for (int i = 0; i < 12; i++) id.append(CALL_CHARS.charAt(RNG.nextInt(CALL_CHARS.length())));
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("op", "call");
        m.put("to", friendId);
        m.put("game", gameKey);
        m.put("kind", kind);
        m.put("call", id.toString());
        return quiet(m) ? id.toString() : "";
    }

    public void answer(String callerId, String callId, boolean ok) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("op", "answer");
        m.put("to", callerId);
        m.put("call", callId);
        m.put("ok", ok);
        quiet(m);
    }

    /** Who has this friend code? The answer comes to onLookup. */
    public void lookup(String code) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("op", "lookup");
        m.put("code", Friends.shortCode(code));
        quiet(m);
    }

    public void request(String friendId) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("op", "friend_request");
        m.put("to", friendId);
        quiet(m);
    }

    public void accept(String friendId) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("op", "friend_accept");
        m.put("to", friendId);
        quiet(m);
    }

    public void cancel(String friendId, String callId) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("op", "cancel_call");
        m.put("to", friendId);
        m.put("call", callId);
        quiet(m);
    }
}
