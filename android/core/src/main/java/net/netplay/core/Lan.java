package net.netplay.core;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Finds a Retro-Netplay matchmaker on the home network: one small UDP broadcast, and matchmakers answer with
 * their port and computer name (server.py, Discovery). Same rules as desktop/lan.py.
 */
public final class Lan {
    private Lan() { }

    public static final String ASK = "RETRO-NETPLAY-DISCOVER/1";
    public static final int DEFAULT_PORT = 7777;

    public static final class Found {
        public final String host, name, id;
        public final int port;
        /** When that matchmaker started (seconds since 1970), or Long.MAX_VALUE if it's an older one that doesn't say. */
        public final long since;
        public Found(String host, int port, String name, long since, String id) {
            this.host = host; this.port = port; this.name = name; this.since = since; this.id = id == null ? "" : id;
        }
        Found(String host, int port, String name) { this(host, port, name, Long.MAX_VALUE, ""); }
        @Override public String toString() { return (name.isEmpty() ? host : name + " (" + host + ")"); }
    }

    private static Found entry(String host, Map<String, Object> m, int fallbackPort) {
        if (!"retro-netplay".equals(m.get("app"))) return null;
        Object port = m.get("port");
        int pn = port instanceof Number ? ((Number) port).intValue() : fallbackPort;
        if (pn < 1 || pn > 65535) return null;
        String name = m.get("name") == null ? "" : Friends.cleanName(String.valueOf(m.get("name")));
        Object since = m.get("since");
        String id = m.get("id") == null ? "" : String.valueOf(m.get("id"));
        if (id.length() > 32) id = id.substring(0, 32);
        return new Found(host, pn, name, since instanceof Number ? ((Number) since).longValue() : Long.MAX_VALUE, id);
    }

    /** One entry per matchmaker: the same one can answer from two addresses (cable and Wi-Fi). */
    private static void merge(Map<String, Found> out, Found f) {
        String key = f.id.isEmpty() ? f.host + ":" + f.port : f.id;
        if (!out.containsKey(key)) out.put(key, f);
    }

    private static long[] addrKey(String host) {
        String[] p = host.split("\\.");
        long[] k = new long[]{999, 0, 0, 0};
        if (p.length != 4) return k;
        try { for (int i = 0; i < 4; i++) k[i] = Long.parseLong(p[i]); } catch (NumberFormatException e) { return new long[]{999, 0, 0, 0}; }
        return k;
    }

    /** The matchmaker everyone on this network should use: running longest, then the lowest address.
     *  The computer app applies the same rule (lan.pick), so everybody ends up on the same one. */
    public static Found pick(List<Found> list) {
        Found best = null;
        for (Found f : list) {
            if (best == null || f.since < best.since) { best = f; continue; }
            if (f.since == best.since) {
                long[] a = addrKey(f.host), b = addrKey(best.host);
                for (int i = 0; i < 4; i++) { if (a[i] != b[i]) { if (a[i] < b[i]) best = f; break; } }
            }
        }
        return best;
    }

    /** Ask one address "are you a Retro-Netplay matchmaker?" over TCP. Blocking. */
    public static Found probe(String host, int port, int timeoutMs) {
        try (java.net.Socket s = new java.net.Socket()) {
            s.connect(new java.net.InetSocketAddress(host, port), timeoutMs);
            s.setSoTimeout(1000);
            s.getOutputStream().write("{\"v\":1,\"op\":\"whoami\"}\n".getBytes(StandardCharsets.UTF_8));
            String line = new java.io.BufferedReader(new java.io.InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8)).readLine();
            if (line == null) return null;
            Map<String, Object> m = Json.parseObject(line);
            if (!"whoami".equals(m.get("op"))) return null;
            if (!m.containsKey("app")) m.put("app", "retro-netplay");     // older matchmakers don't say
            m.put("port", port);
            return entry(host, m, port);
        } catch (Exception e) {
            return null;
        }
    }

    /** When the router drops broadcasts: knock on every address of this home network (x.x.x.1 - .254). Blocking. */
    public static List<Found> sweep(String myIp, int port, int timeoutMs) {
        List<Found> res = java.util.Collections.synchronizedList(new ArrayList<>());
        if (myIp == null || !myIp.matches("^\\d{1,3}(\\.\\d{1,3}){3}$") || !isPrivate(myIp)) return new ArrayList<>();
        String base = myIp.substring(0, myIp.lastIndexOf('.') + 1);
        java.util.concurrent.ExecutorService ex = java.util.concurrent.Executors.newFixedThreadPool(48);
        for (int n = 1; n < 255; n++) {
            final String h = base + n;
            if (h.equals(myIp)) continue;
            ex.execute(() -> { Found f = probe(h, port, timeoutMs); if (f != null) res.add(f); });
        }
        ex.shutdown();
        try { ex.awaitTermination(15, java.util.concurrent.TimeUnit.SECONDS); } catch (InterruptedException ignored) { }
        Map<String, Found> out = new LinkedHashMap<>();
        synchronized (res) { for (Found f : res) merge(out, f); }
        return new ArrayList<>(out.values());
    }

    /** Broadcast first; if nothing answers, try every address on the network. Blocking. */
    public static List<Found> findAll(List<String> targets, String myIp, int port) {
        List<Found> r = discover(targets, 1500, port + 2);
        return r.isEmpty() ? sweep(myIp, port, 400) : r;
    }

    /** 127.0.0.1, localhost and friends: an address that means "this same device". */
    public static boolean isLoopback(String host) {
        String h = host == null ? "" : host.trim().toLowerCase(Locale.ROOT);
        return h.isEmpty() || h.equals("localhost") || h.equals("0.0.0.0") || h.equals("::1") || h.startsWith("127.");
    }

    /** targets: broadcast addresses to ask (e.g. "255.255.255.255" and "192.168.1.255"). Blocking; call off the UI thread. */
    public static List<Found> discover(List<String> targets, int timeoutMs, int discoveryPort) {
        Map<String, Found> out = new LinkedHashMap<>();
        try (DatagramSocket s = new DatagramSocket()) {
            s.setBroadcast(true);
            s.setSoTimeout(200);
            byte[] ask = ASK.getBytes(StandardCharsets.US_ASCII);
            long end = System.currentTimeMillis() + timeoutMs;
            long start = System.currentTimeMillis();
            int sent = 0;
            byte[] buf = new byte[2048];
            while (System.currentTimeMillis() < end) {
                if (sent < 3 && System.currentTimeMillis() >= start + sent * 400L) {
                    for (String t : targets) {
                        try { s.send(new DatagramPacket(ask, ask.length, InetAddress.getByName(t), discoveryPort)); }
                        catch (Exception ignored) { }
                    }
                    sent++;
                }
                DatagramPacket p = new DatagramPacket(buf, buf.length);
                try { s.receive(p); } catch (SocketTimeoutException e) { continue; }
                Map<String, Object> m;
                try { m = Json.parseObject(new String(p.getData(), 0, p.getLength(), StandardCharsets.UTF_8)); }
                catch (RuntimeException e) { continue; }
                Found f = entry(p.getAddress().getHostAddress(), m, -1);
                if (f != null) merge(out, f);
            }
        } catch (Exception ignored) { }
        return new ArrayList<>(out.values());
    }

    private static final java.util.regex.Pattern ADDR = java.util.regex.Pattern.compile(
            "(?<![\\w.])((?:\\d{1,3}\\.){3}\\d{1,3}|[A-Za-z0-9-]+(?:\\.[A-Za-z0-9-]+)+)(?::(\\d{2,5}))?(?![\\w.])");
    private static final java.util.regex.Pattern IPV4 = java.util.regex.Pattern.compile("^\\d{1,3}(\\.\\d{1,3}){3}$");

    /** "192.168.1.23", "1.2.3.4:7777" or "Retro-Netplay: connect to 1.2.3.4:7777" -> {host, port}, or null. Same as lan.py. */
    public static String[] parseAddress(String text) {
        if (text == null) return null;
        java.util.regex.Matcher m = ADDR.matcher(text);
        if (!m.find()) return null;
        String host = m.group(1);
        int port = m.group(2) == null ? DEFAULT_PORT : Integer.parseInt(m.group(2));
        if (IPV4.matcher(host).matches()) for (String x : host.split("\\.")) if (Integer.parseInt(x) > 255) return null;
        if (port < 1 || port > 65535 || isLoopback(host)) return null;
        return new String[]{host, String.valueOf(port)};
    }

    public static String shareText(String host, int port) { return "Retro-Netplay: connect to " + host + ":" + port; }

    public static boolean isPrivate(String ip) {
        return ip.startsWith("10.") || ip.startsWith("192.168.") || ip.matches("^172\\.(1[6-9]|2\\d|3[01])\\..*")
                || ip.startsWith("169.254.") || ip.startsWith("100.64.");
    }

    /** The address a matchmaker on the internet sees for you ("whoami"); "" if unknown or it's on your home network. */
    public static String whoami(String host, int port) {
        if (isLoopback(host) || isPrivate(host)) return "";
        try (java.net.Socket s = new java.net.Socket()) {
            s.connect(new java.net.InetSocketAddress(host, port), 4000);
            s.setSoTimeout(4000);
            s.getOutputStream().write("{\"v\":1,\"op\":\"whoami\"}\n".getBytes(StandardCharsets.UTF_8));
            String line = new java.io.BufferedReader(new java.io.InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8)).readLine();
            Object ip = line == null ? null : Json.parseObject(line).get("ip");
            String v = ip == null ? "" : String.valueOf(ip);
            return IPV4.matcher(v).matches() && !isPrivate(v) ? v : "";
        } catch (Exception e) {
            return "";
        }
    }

    /** Ask a public "what's my IP" service. Only when the person presses the button: that service sees the address. */
    public static final String LOOKUP_URL = "https://api.ipify.org";

    public static String lookupOnline() {
        try {
            java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL(LOOKUP_URL).openConnection();
            c.setConnectTimeout(5000);
            c.setReadTimeout(5000);
            byte[] b = new byte[64];
            int n;
            try (java.io.InputStream in = c.getInputStream()) { n = in.read(b); }
            String v = n > 0 ? new String(b, 0, n, StandardCharsets.US_ASCII).trim() : "";
            return IPV4.matcher(v).matches() ? v : "";
        } catch (Exception e) {
            return "";
        }
    }

    public static List<Found> discover(List<String> targets) {
        return discover(targets, 1500, DEFAULT_PORT + 2);
    }
}
