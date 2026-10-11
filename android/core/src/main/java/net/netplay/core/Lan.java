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

    public static List<Found> discover(List<String> targets) {
        return discover(targets, 1500, DEFAULT_PORT + 2);
    }
}
