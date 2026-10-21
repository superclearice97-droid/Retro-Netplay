package net.netplay.core;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * The stream connection to the matchmaker (PROTOCOL.md, Built-in streaming): after a match, the player opens one with
 * its own session key and swaps WebRTC setup messages (hello / offer / answer / ice / status / bye) with the host
 * through it. The answer to the first line carries the ICE servers (STUN, and a short-lived relay password).
 * Same behaviour as the desktop's stream_signal.py, including reconnecting after a network hiccup.
 */
public final class StreamSignal {
    public interface Listener {
        void onMessage(Map<String, Object> msg);
        /** Once: "session closed" (after a goodbye), "session ended", "lost contact with the matchmaker", or the matchmaker's words. */
        void onEnd(String why);
    }

    /** why: bad_key, expired, busy, unreachable, tls, old. */
    public static final class Failure extends IOException {
        private static final long serialVersionUID = 1L;
        public final String why;
        public Failure(String message, String why) { super(message); this.why = why; }
    }

    private static final Map<String, Object> STOP = new LinkedHashMap<>();
    private static final int PING_MS = 20000;

    private final String host, key;
    private final int port;
    private final Listener listener;
    private final LinkedBlockingQueue<Map<String, Object>> out = new LinkedBlockingQueue<>();
    private final AtomicInteger unsent = new AtomicInteger();
    private volatile Socket socket;
    private volatile BufferedReader in;
    private volatile boolean stopped, ended, byeSeen;
    public volatile Map<String, Object> config = new LinkedHashMap<>();

    public StreamSignal(String host, int port, String key, Listener listener) {
        this.host = host; this.port = port; this.key = key; this.listener = listener;
    }

    /** Open the connection; returns the matchmaker's answer ({"role", "game", "iceServers", "relay"}). */
    public Map<String, Object> connect() throws Failure {
        Socket s;
        try {
            s = MmTls.connect(host, port, 8000);
        } catch (MmTls.TlsException e) {
            throw new Failure(e.getMessage(), "tls");
        } catch (IOException e) {
            throw new Failure("Couldn't reach the matchmaker at " + host + ":" + port + " (" + e.getMessage() + ").", "unreachable");
        }
        Map<String, Object> reply;
        BufferedReader r;
        try {
            s.setSoTimeout(8000);
            Map<String, Object> hello = new LinkedHashMap<>();
            hello.put("v", 1L);
            hello.put("op", "stream");
            hello.put("key", key);
            OutputStream o = s.getOutputStream();
            o.write((Json.stringify(hello) + "\n").getBytes(StandardCharsets.UTF_8));
            o.flush();
            r = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
            String line = r.readLine();
            reply = line == null ? null : Json.parseObject(line);
        } catch (IOException | RuntimeException e) {
            close(s);
            throw new Failure("The matchmaker didn't answer (" + e.getMessage() + ").", "unreachable");
        }
        if (reply == null || !"stream".equals(reply.get("op"))) {
            close(s);
            if (reply != null && "error".equals(reply.get("op"))) {
                throw new Failure(String.valueOf(reply.get("msg")), reply.get("why") == null ? "" : String.valueOf(reply.get("why")));
            }
            throw new Failure("This matchmaker doesn't support the stream connection (it may be an older version).", "old");
        }
        try {
            s.setSoTimeout(PING_MS * 7 / 2);
        } catch (IOException ignored) { }
        socket = s;
        in = r;
        config = reply;
        return reply;
    }

    public void start() {
        thread("stream-read", this::readLoop);
        thread("stream-send", this::sendLoop);
        thread("stream-ping", () -> {
            while (!stopped) {
                try { Thread.sleep(PING_MS); } catch (InterruptedException e) { return; }
                Map<String, Object> ping = new LinkedHashMap<>();
                ping.put("op", "ping");
                enqueue(ping);
            }
        });
    }

    /** Queue a setup message for the other side. */
    public void send(Map<String, Object> msg) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("op", "sig");
        m.put("msg", msg);
        enqueue(m);
    }

    /** Wait (a little) until everything queued has really been written (the goodbye must not be lost). */
    public void flush(long ms) {
        long end = System.currentTimeMillis() + ms;
        while (unsent.get() > 0 && System.currentTimeMillis() < end) {
            try { Thread.sleep(20); } catch (InterruptedException e) { return; }
        }
    }

    public void stop() {
        stopped = true;
        out.offer(STOP);
        close(socket);
    }

    private void enqueue(Map<String, Object> m) {
        unsent.incrementAndGet();
        out.offer(m);
    }

    private void sendLoop() {
        Map<String, Object> pending = null;
        while (!stopped) {
            Map<String, Object> item;
            try { item = pending != null ? pending : out.take(); } catch (InterruptedException e) { return; }
            if (item == STOP) return;
            try {
                Socket s = socket;
                if (s == null) throw new IOException("not connected");
                OutputStream o = s.getOutputStream();
                o.write((Json.stringify(item) + "\n").getBytes(StandardCharsets.UTF_8));
                o.flush();
                pending = null;
                unsent.decrementAndGet();
            } catch (IOException e) {
                pending = item;            // sent again once the connection is back
                try { Thread.sleep(200); } catch (InterruptedException ie) { return; }
            }
        }
    }

    private void readLoop() {
        while (!stopped) {
            String line;
            try {
                line = in.readLine();
            } catch (IOException e) {
                line = null;
            }
            if (stopped) return;
            if (line == null) {
                if (byeSeen) { end("session closed"); return; }
                if (!reconnect()) return;
                continue;
            }
            Map<String, Object> m;
            try { m = Json.parseObject(line); } catch (RuntimeException e) { continue; }
            Object op = m.get("op");
            if ("sig".equals(op) && m.get("msg") instanceof Map) {
                @SuppressWarnings("unchecked") Map<String, Object> msg = (Map<String, Object>) m.get("msg");
                if ("bye".equals(msg.get("type"))) byeSeen = true;
                listener.onMessage(msg);
            } else if ("error".equals(op)) {
                end(m.get("msg") == null ? "the matchmaker ended the stream" : String.valueOf(m.get("msg")));
                return;
            }
        }
    }

    /** The connection dropped (Wi-Fi hiccup, matchmaker restart): try again for a while with the same key. */
    private boolean reconnect() {
        long end = System.currentTimeMillis() + 20000, delay = 500;
        while (!stopped && System.currentTimeMillis() < end) {
            try {
                connect();
                return true;
            } catch (Failure f) {
                if ("bad_key".equals(f.why) || "expired".equals(f.why)) { end("session ended"); return false; }
            }
            try { Thread.sleep(delay); } catch (InterruptedException e) { return false; }
            delay = Math.min(delay * 2, 4000);
        }
        end("lost contact with the matchmaker");
        return false;
    }

    private synchronized void end(String why) {
        if (ended) return;
        ended = true;
        stop();
        listener.onEnd(why);
    }

    private static void thread(String name, Runnable r) {
        Thread t = new Thread(r, name);
        t.setDaemon(true);
        t.start();
    }

    private static void close(Socket s) {
        if (s == null) return;
        try { s.close(); } catch (IOException ignored) { }
    }
}
