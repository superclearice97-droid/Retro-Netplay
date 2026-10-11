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
        public final String host, name;
        public final int port;
        Found(String host, int port, String name) { this.host = host; this.port = port; this.name = name; }
        @Override public String toString() { return (name.isEmpty() ? host : name + " (" + host + ")"); }
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
                Object port = m.get("port");
                if (!"retro-netplay".equals(m.get("app")) || !(port instanceof Number)) continue;
                int pn = ((Number) port).intValue();
                if (pn < 1 || pn > 65535) continue;
                String host = p.getAddress().getHostAddress();
                String name = m.get("name") == null ? "" : Friends.cleanName(String.valueOf(m.get("name")));
                out.put(host + ":" + pn, new Found(host, pn, name));
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
