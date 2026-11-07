package net.netplay.core;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * The interface's local API on the phone, the same as the desktop engine's (engine.py, class Api): the page and its
 * files, /api/state, /api/events (Server-Sent Events), /api/do, and /art for the library's own pictures. Only on
 * 127.0.0.1, with a random token, and only for requests addressed to 127.0.0.1 (so another site can't use it).
 */
public final class LocalApi {
    /** Files of the interface (index.html, app.js, ...), or null. */
    public interface Files { byte[] read(String path); }
    /** A picture from the library, or null when it isn't one of the library's own. */
    public interface Art { byte[] read(String path); }

    private final Arcade arcade;
    private final Files files;
    private final Art art;
    public final String token;
    private ServerSocket server;
    public int port;

    public LocalApi(Arcade arcade, Files files, Art art) {
        this.arcade = arcade;
        this.files = files;
        this.art = art;
        byte[] b = new byte[18];
        new SecureRandom().nextBytes(b);
        StringBuilder t = new StringBuilder();
        for (byte x : b) t.append(String.format("%02x", x));
        token = t.toString();
    }

    public String url() { return "http://127.0.0.1:" + port + "/#t=" + token; }

    public void start() throws IOException {
        server = new ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"));
        port = server.getLocalPort();
        Thread t = new Thread(() -> {
            while (!server.isClosed()) {
                try {
                    Socket c = server.accept();
                    Thread h = new Thread(() -> handle(c), "api");
                    h.setDaemon(true);
                    h.start();
                } catch (IOException e) { return; }
            }
        }, "api-accept");
        t.setDaemon(true);
        t.start();
    }

    public void stop() { try { if (server != null) server.close(); } catch (IOException ignored) { } }

    private void handle(Socket c) {
        try (Socket s = c) {
            s.setSoTimeout(15000);
            InputStream in = new BufferedInputStream(s.getInputStream());
            String head = readHead(in);
            if (head == null) return;
            String[] lines = head.split("\r\n");
            String[] first = lines[0].split(" ");
            if (first.length < 2) return;
            String method = first[0], target = first[1];
            Map<String, String> hdr = new LinkedHashMap<>();
            for (int i = 1; i < lines.length; i++) {
                int k = lines[i].indexOf(':');
                if (k > 0) hdr.put(lines[i].substring(0, k).trim().toLowerCase(), lines[i].substring(k + 1).trim());
            }
            OutputStream out = s.getOutputStream();
            String host = hdr.getOrDefault("host", "");
            if (!host.equals("127.0.0.1:" + port) && !host.equals("localhost:" + port)) { reply(out, 403, "text/plain", "wrong host".getBytes()); return; }
            String path = target, query = "";
            int q = target.indexOf('?');
            if (q >= 0) { path = target.substring(0, q); query = target.substring(q + 1); }
            if (path.startsWith("/api/") || path.equals("/art")) {
                String tok = hdr.containsKey("x-token") ? hdr.get("x-token") : param(query, "t");
                if (!same(tok, token)) { reply(out, 403, "text/plain", "no".getBytes()); return; }
            }
            if ("GET".equals(method) && path.equals("/api/state")) {
                reply(out, 200, "application/json", Json.stringify(arcade.snapshot()).getBytes(StandardCharsets.UTF_8));
            } else if ("GET".equals(method) && path.equals("/api/events")) {
                s.setSoTimeout(0);
                events(out);
            } else if ("POST".equals(method) && path.equals("/api/do")) {
                int n = 0;
                try { n = Math.min(Integer.parseInt(hdr.getOrDefault("content-length", "0")), 65536); } catch (NumberFormatException ignored) { }
                byte[] body = new byte[n];
                int got = 0;
                while (got < n) { int r = in.read(body, got, n - got); if (r < 0) break; got += r; }
                Map<String, Object> res;
                try { res = arcade.act(Json.parseObject(new String(body, 0, got, StandardCharsets.UTF_8))); }
                catch (RuntimeException e) { res = Arcade.fail("bad request"); }
                reply(out, 200, "application/json", Json.stringify(res).getBytes(StandardCharsets.UTF_8));
            } else if ("GET".equals(method) && path.equals("/art")) {
                byte[] b = art.read(param(query, "p"));
                if (b == null) reply(out, 404, "text/plain", new byte[0]); else reply(out, 200, mime(param(query, "p")), b);
            } else if ("GET".equals(method)) {
                String rel = path.equals("/") || path.isEmpty() ? "index.html" : path.substring(1);
                byte[] b = rel.contains("..") ? null : files.read(rel);
                if (b == null) reply(out, 404, "text/plain", "not found".getBytes()); else reply(out, 200, mime(rel), b);
            } else reply(out, 404, "text/plain", "not found".getBytes());
        } catch (IOException | RuntimeException ignored) { }
    }

    private void events(OutputStream out) throws IOException {
        BlockingQueue<Object[]> q = new ArrayBlockingQueue<>(200);
        Arcade.Listener l = (name, data) -> q.offer(new Object[]{name, data});
        arcade.listen(l);
        try {
            out.write("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-store\r\n\r\n".getBytes(StandardCharsets.UTF_8));
            out.write(("event: state\ndata: " + Json.stringify(arcade.snapshot()) + "\n\n").getBytes(StandardCharsets.UTF_8));
            out.flush();
            while (true) {
                Object[] e;
                try { e = q.poll(15, TimeUnit.SECONDS); } catch (InterruptedException x) { return; }
                out.write((e == null ? ": still here\n\n" : "event: " + e[0] + "\ndata: " + Json.stringify(e[1]) + "\n\n").getBytes(StandardCharsets.UTF_8));
                out.flush();
            }
        } finally { arcade.unlisten(l); }
    }

    private static String readHead(InputStream in) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        int c, state = 0;
        while ((c = in.read()) >= 0) {
            b.write(c);
            if (b.size() > 16384) return null;
            state = (c == '\r' && (state == 0 || state == 2)) ? state + 1 : (c == '\n' && (state == 1 || state == 3)) ? state + 1 : 0;
            if (state == 4) return b.toString("ISO-8859-1");
        }
        return null;
    }

    private static void reply(OutputStream out, int status, String type, byte[] body) throws IOException {
        out.write(("HTTP/1.1 " + status + " OK\r\nContent-Type: " + type + "\r\nContent-Length: " + body.length + "\r\n"
                + "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
                + "Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'\r\n"
                + "Connection: close\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        out.write(body);
        out.flush();
    }

    static String param(String query, String key) {
        for (String part : query.split("&")) {
            int eq = part.indexOf('=');
            if (eq > 0 && part.substring(0, eq).equals(key)) {
                try { return URLDecoder.decode(part.substring(eq + 1), "UTF-8"); } catch (Exception e) { return ""; }
            }
        }
        return "";
    }

    private static boolean same(String a, String b) {
        return a != null && MessageDigest.isEqual(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8));
    }

    static String mime(String p) {
        String l = p.toLowerCase();
        if (l.endsWith(".html")) return "text/html; charset=utf-8";
        if (l.endsWith(".js")) return "text/javascript; charset=utf-8";
        if (l.endsWith(".css")) return "text/css; charset=utf-8";
        if (l.endsWith(".png")) return "image/png";
        if (l.endsWith(".jpg") || l.endsWith(".jpeg")) return "image/jpeg";
        if (l.endsWith(".webp")) return "image/webp";
        if (l.endsWith(".wav")) return "audio/wav";
        if (l.endsWith(".svg")) return "image/svg+xml";
        return "application/octet-stream";
    }
}
