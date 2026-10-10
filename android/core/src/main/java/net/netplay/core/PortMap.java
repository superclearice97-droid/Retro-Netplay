package net.netplay.core;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Proxy;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Opens a port on the home router automatically (UPnP, then NAT-PMP) and checks it through the matchmaker.
 * Same behaviour as desktop/portmap.py. Only talks to devices on the local network and to the chosen matchmaker.
 */
public final class PortMap {
    public static final int RETROARCH_PORT = 55435;
    static final int LEASE = 7200;
    private static final String[] WAN = {"urn:schemas-upnp-org:service:WANIPConnection:2",
            "urn:schemas-upnp-org:service:WANIPConnection:1", "urn:schemas-upnp-org:service:WANPPPConnection:1"};
    private static final int MAX_XML = 256 * 1024;

    public static final class Result {
        public final boolean ok;
        public final String method, message;
        Result(boolean ok, String method, String message) { this.ok = ok; this.method = method; this.message = message; }
    }

    public static final class Check {
        /** TRUE open, FALSE closed, null couldn't test. */
        public final Boolean open;
        public final String ip, message;
        Check(Boolean open, String ip, String message) { this.open = open; this.ip = ip; this.message = message; }
    }

    public static final class Gateway {
        public final String controlUrl, service, localIp, name;
        Gateway(String c, String s, String l, String n) { controlUrl = c; service = s; localIp = l; name = n; }
    }

    // ------------------------------------------------------------------ helpers
    static boolean isPrivate(String host) {
        if (host == null || !host.matches("\\d{1,3}(\\.\\d{1,3}){3}")) return false;
        try {
            InetAddress a = InetAddress.getByName(host);   // a literal: no DNS lookup
            return a.isSiteLocalAddress() || a.isLinkLocalAddress() || a.isLoopbackAddress();
        } catch (Exception e) { return false; }
    }

    private static String localIpTowards(String host) {
        try (DatagramSocket s = new DatagramSocket()) {
            s.connect(new InetSocketAddress(host, 9));
            return s.getLocalAddress().getHostAddress();
        } catch (Exception e) { return ""; }
    }

    private static String readLimited(InputStream in) throws java.io.IOException {
        if (in == null) return "";
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) > 0 && out.size() < MAX_XML) out.write(buf, 0, n);
        return new String(out.toByteArray(), StandardCharsets.UTF_8);
    }

    private static String get(String url) throws java.io.IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection(Proxy.NO_PROXY);
        c.setConnectTimeout(3000);
        c.setReadTimeout(3000);
        try (InputStream in = c.getInputStream()) { return readLimited(in); } finally { c.disconnect(); }
    }

    private static String tag(String xml, String name) {
        Matcher m = Pattern.compile("<(?:[A-Za-z0-9_]+:)?" + name + "\\b[^>]*>([^<]*)</", Pattern.CASE_INSENSITIVE).matcher(xml);
        return m.find() ? m.group(1).trim() : "";
    }

    /** -> control URL, service type, friendly name for the first WAN connection service; null if none. */
    static String[] parseDescription(String xml, String location) throws Exception {
        String base = tag(xml, "URLBase");
        if (base.isEmpty()) base = location;
        String name = tag(xml, "friendlyName");
        Map<String, String> found = new LinkedHashMap<>();
        Matcher svc = Pattern.compile("<(?:[A-Za-z0-9_]+:)?service>(.*?)</(?:[A-Za-z0-9_]+:)?service>", Pattern.DOTALL).matcher(xml);
        while (svc.find()) {
            String block = svc.group(1), type = tag(block, "serviceType"), ctl = tag(block, "controlURL");
            if (!ctl.isEmpty()) found.put(type, new URL(new URL(base), ctl).toString());
        }
        for (String w : WAN) if (found.containsKey(w)) return new String[]{found.get(w), w, name};
        return null;
    }

    // ------------------------------------------------------------------ UPnP
    public static Gateway discover(int timeoutMs, List<String> locations) {
        if (locations == null) {
            locations = new ArrayList<>();
            try (DatagramSocket s = new DatagramSocket()) {
                s.setSoTimeout(400);
                InetSocketAddress ssdp = new InetSocketAddress("239.255.255.250", 1900);
                for (String st : new String[]{"urn:schemas-upnp-org:device:InternetGatewayDevice:1",
                        "urn:schemas-upnp-org:device:InternetGatewayDevice:2", "urn:schemas-upnp-org:service:WANIPConnection:1"}) {
                    byte[] m = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: " + st + "\r\n\r\n")
                            .getBytes(StandardCharsets.US_ASCII);
                    s.send(new DatagramPacket(m, m.length, ssdp));
                }
                long end = System.currentTimeMillis() + timeoutMs;
                byte[] buf = new byte[4096];
                while (System.currentTimeMillis() < end) {
                    DatagramPacket p = new DatagramPacket(buf, buf.length);
                    try { s.receive(p); } catch (SocketTimeoutException t) { continue; }
                    for (String line : new String(p.getData(), 0, p.getLength(), StandardCharsets.UTF_8).split("\r\n")) {
                        if (line.toLowerCase().startsWith("location:")) {
                            String loc = line.substring(9).trim();
                            if (!locations.contains(loc)) locations.add(loc);
                        }
                    }
                }
            } catch (Exception ignored) { }
        }
        for (String loc : locations) {
            try {
                String host = new URL(loc).getHost();
                if (!isPrivate(host)) continue;     // only devices on the home network
                String[] got = parseDescription(get(loc), loc);
                if (got == null || !isPrivate(new URL(got[0]).getHost())) continue;
                return new Gateway(got[0], got[1], localIpTowards(host), got[2]);
            } catch (Exception ignored) { }
        }
        return null;
    }

    private static Object[] soap(Gateway g, String action, String[][] args) throws java.io.IOException {
        StringBuilder body = new StringBuilder();
        for (String[] a : args) body.append('<').append(a[0]).append('>').append(a[1]).append("</").append(a[0]).append('>');
        String env = "<?xml version=\"1.0\"?><s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\" "
                + "s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\"><s:Body><u:" + action + " xmlns:u=\"" + g.service + "\">"
                + body + "</u:" + action + "></s:Body></s:Envelope>";
        HttpURLConnection c = (HttpURLConnection) new URL(g.controlUrl).openConnection(Proxy.NO_PROXY);
        c.setConnectTimeout(4000);
        c.setReadTimeout(5000);
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "text/xml; charset=\"utf-8\"");
        c.setRequestProperty("SOAPAction", "\"" + g.service + "#" + action + "\"");
        try {
            try (OutputStream o = c.getOutputStream()) { o.write(env.getBytes(StandardCharsets.UTF_8)); }
            int code = c.getResponseCode();
            String text = readLimited(code < 400 ? c.getInputStream() : c.getErrorStream());
            return new Object[]{code, text};
        } finally { c.disconnect(); }
    }

    /** -> null on success, else a plain-language reason. */
    public static String add(Gateway g, int port, String proto, int lease) {
        try {
            Object[] r = soap(g, "AddPortMapping", new String[][]{{"NewRemoteHost", ""}, {"NewExternalPort", "" + port},
                    {"NewProtocol", proto}, {"NewInternalPort", "" + port}, {"NewInternalClient", g.localIp}, {"NewEnabled", "1"},
                    {"NewPortMappingDescription", "Retro-Netplay"}, {"NewLeaseDuration", "" + lease}});
            if ((Integer) r[0] == 200) return null;
            String err = tag((String) r[1], "errorCode");
            if (err.equals("725") && lease != 0) return add(g, port, proto, 0);
            if (err.equals("718")) return "another device on your network is already using this port";
            return "router refused (error " + (err.isEmpty() ? r[0] : err) + ")";
        } catch (Exception e) { return "router didn't answer"; }
    }

    public static void delete(Gateway g, int port, String proto) {
        try { soap(g, "DeletePortMapping", new String[][]{{"NewRemoteHost", ""}, {"NewExternalPort", "" + port}, {"NewProtocol", proto}}); }
        catch (Exception ignored) { }
    }

    public static String externalIp(Gateway g) {
        try {
            Object[] r = soap(g, "GetExternalIPAddress", new String[0][]);
            return (Integer) r[0] == 200 ? tag((String) r[1], "NewExternalIPAddress") : "";
        } catch (Exception e) { return ""; }
    }

    // ------------------------------------------------------------------ NAT-PMP
    public static boolean natpmp(String gateway, int port, String proto, int lease) {
        if (!isPrivate(gateway)) return false;
        int op = proto.equals("TCP") ? 2 : 1;
        try (DatagramSocket s = new DatagramSocket()) {
            s.setSoTimeout(1000);
            ByteBuffer b = ByteBuffer.allocate(12);
            b.put((byte) 0).put((byte) op).putShort((short) 0).putShort((short) port).putShort((short) port).putInt(lease);
            s.send(new DatagramPacket(b.array(), 12, new InetSocketAddress(gateway, 5351)));
            byte[] in = new byte[64];
            DatagramPacket p = new DatagramPacket(in, in.length);
            s.receive(p);
            if (p.getLength() < 16 || !p.getAddress().getHostAddress().equals(gateway)) return false;
            ByteBuffer r = ByteBuffer.wrap(in);
            r.get();
            int rop = r.get() & 0xFF, result = r.getShort() & 0xFFFF;
            r.getInt(); r.getShort();
            int eport = r.getShort() & 0xFFFF;
            return result == 0 && rop == 128 + op && eport == port;
        } catch (Exception e) { return false; }
    }

    /** Try UPnP, then NAT-PMP (gateway may be "" when unknown). */
    public static Result open(int port, String proto, String gatewayIp, List<String> locations) {
        Gateway g = discover(2500, locations);
        String why = "";
        if (g != null) {
            why = add(g, port, proto, LEASE);
            if (why == null) return new Result(true, "UPnP", "Your router opened port " + port + " automatically.");
        }
        if (natpmp(gatewayIp, port, proto, LEASE)) return new Result(true, "NAT-PMP", "Your router opened port " + port + " automatically.");
        return new Result(false, "", "Your router didn't allow automatic setup" + (why.isEmpty() ? "" : " (" + why + ")")
                + ". You can still join other people's games and streams. To host, turn on UPnP in your router's settings, or forward port "
                + port + " " + proto + " to this phone.");
    }

    // ------------------------------------------------------------------ connect-back check
    public static Check check(String serverHost, int serverPort, int port) {
        byte[] t = new byte[12];
        new SecureRandom().nextBytes(t);
        StringBuilder hex = new StringBuilder();
        for (byte x : t) hex.append(String.format("%02x", x));
        final String token = hex.toString();
        final ServerSocket lst;
        try {
            lst = new ServerSocket();
            lst.setReuseAddress(true);
            lst.bind(new InetSocketAddress(port));
            lst.setSoTimeout(9000);
        } catch (Exception e) {
            return new Check(null, "", "Port " + port + " is in use by another app right now, so it can't be tested.");
        }
        Thread answer = new Thread(() -> {
            try {
                while (!lst.isClosed()) {
                    try (Socket c = lst.accept()) { c.getOutputStream().write((token + "\n").getBytes(StandardCharsets.US_ASCII)); }
                }
            } catch (Exception ignored) { }
        });
        answer.setDaemon(true);
        answer.start();
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress(serverHost, serverPort), 6000);
            s.setSoTimeout(9000);
            s.getOutputStream().write(("{\"v\":1,\"op\":\"probe\",\"port\":" + port + ",\"token\":\"" + token + "\"}\n").getBytes(StandardCharsets.UTF_8));
            InputStream in = s.getInputStream();
            ByteArrayOutputStream line = new ByteArrayOutputStream();
            int ch;
            while ((ch = in.read()) >= 0 && ch != '\n' && line.size() < 4096) line.write(ch);
            Object o = Json.parse(new String(line.toByteArray(), StandardCharsets.UTF_8));
            if (!(o instanceof Map)) return new Check(null, "", "The matchmaker didn't answer the port test.");
            Map<?, ?> m = (Map<?, ?>) o;
            if ("error".equals(m.get("op"))) return new Check(null, "", "The matchmaker can't test ports.");
            boolean open = Boolean.TRUE.equals(m.get("open"));
            String ip = m.get("ip") instanceof String ? (String) m.get("ip") : "";
            return new Check(open, ip, open ? "Port " + port + " is open. Other players can connect to you."
                    : "Port " + port + " is still closed from the internet. You can join others, but not host netplay.");
        } catch (Exception e) {
            return new Check(null, "", "Couldn't reach the matchmaker to test the port.");
        } finally {
            try { lst.close(); } catch (Exception ignored) { }
        }
    }
}
