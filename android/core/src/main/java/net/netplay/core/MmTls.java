package net.netplay.core;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/**
 * Encrypted connections to a matchmaker on the internet (PROTOCOL.md, "Encryption"). Same rules as the desktop app:
 * ask for TLS ("starttls") only when the matchmaker is reached by an internet name, check its certificate, and never
 * go back to plain text with a matchmaker once it was seen with TLS. Home-network matchmakers stay plain.
 */
public final class MmTls {
    private MmTls() { }

    /** Which matchmakers were seen with TLS (the app keeps this in its settings; in memory otherwise). */
    public interface Seen {
        boolean has(String host);
        void add(String host);
    }

    public static volatile Seen seen = new Seen() {
        private final Set<String> s = Collections.synchronizedSet(new HashSet<>());
        @Override public boolean has(String host) { return s.contains(host); }
        @Override public void add(String host) { s.add(host); }
    };
    /** Which matchmakers to ask for TLS (automatic tests also use it for localhost). */
    public static volatile java.util.function.Predicate<String> rule = MmTls::wantsTls;
    /** (automatic tests: a factory that trusts a test certificate authority) */
    public static volatile SSLSocketFactory factory = (SSLSocketFactory) SSLSocketFactory.getDefault();

    public static final class TlsException extends IOException {
        private static final long serialVersionUID = 1L;
        public TlsException(String m) { super(m); }
    }

    private static final Pattern IPV4 = Pattern.compile("^[0-9]{1,3}(\\.[0-9]{1,3}){3}$");
    private static final String[] LOCAL = {".local", ".lan", ".home", ".internal", ".localdomain"};

    /** Yes for internet names; no for addresses and home-network names (certificates are made for names). */
    public static boolean wantsTls(String host) {
        String h = host == null ? "" : host.trim().toLowerCase(Locale.ROOT);
        if (h.startsWith("[") || h.contains(":") || IPV4.matcher(h).matches()) return false;
        if (h.isEmpty() || h.equals("localhost") || !h.contains(".")) return false;
        for (String suffix : LOCAL) if (h.endsWith(suffix)) return false;
        return true;
    }

    /** A connected socket to the matchmaker, encrypted when it offers TLS. */
    public static Socket connect(String host, int port, int timeoutMs) throws IOException {
        Socket s = plain(host, port, timeoutMs);
        if (!rule.test(host)) return ready(s);
        String key = host.toLowerCase(Locale.ROOT);
        Map<String, Object> reply;
        try {
            OutputStream out = s.getOutputStream();
            out.write("{\"v\":1,\"op\":\"starttls\"}\n".getBytes(StandardCharsets.UTF_8));
            out.flush();
            reply = Json.parseObject(line(s.getInputStream()));
        } catch (IOException | RuntimeException e) {
            reply = Collections.emptyMap();
        }
        if ("starttls".equals(reply.get("op")) && Boolean.TRUE.equals(reply.get("ok"))) {
            try {
                SSLSocket tls = (SSLSocket) factory.createSocket(s, host, port, true);
                SSLParameters p = tls.getSSLParameters();
                p.setEndpointIdentificationAlgorithm("HTTPS");     // the certificate must be for this name
                tls.setSSLParameters(p);
                tls.startHandshake();
                HostnameVerifier v = HttpsURLConnection.getDefaultHostnameVerifier();   // (Android's checks names too;
                boolean jdk = v.getClass().getName().startsWith("javax.net.ssl.");    //  the plain JDK's rejects all)
                if (!jdk && !v.verify(host, tls.getSession())) {
                    tls.close();
                    throw new TlsException("The matchmaker " + host + "'s certificate is for a different name.");
                }
                seen.add(key);
                return ready(tls);
            } catch (TlsException e) {
                throw e;
            } catch (IOException e) {
                try { s.close(); } catch (IOException ignored) { }
                throw new TlsException("The matchmaker " + host + "'s certificate doesn't check out (" + e.getMessage() + ").");
            }
        }
        if (seen.has(key)) {
            s.close();
            throw new TlsException("The matchmaker " + host + " used to be encrypted and now isn't. Someone may be in between; not connecting.");
        }
        if (!"starttls".equals(reply.get("op"))) {      // an older matchmaker that didn't understand: start over in plain text
            s.close();
            return ready(plain(host, port, timeoutMs));
        }
        return ready(s);                                // it said "no TLS here": carry on in plain text on this connection
    }

    /** The caller sets its own read timeout (a search waits much longer than the connection takes). */
    private static Socket ready(Socket s) throws IOException {
        s.setSoTimeout(0);
        return s;
    }

    private static Socket plain(String host, int port, int timeoutMs) throws IOException {
        Socket s = new Socket();
        s.connect(new InetSocketAddress(host, port), timeoutMs);
        s.setSoTimeout(timeoutMs);
        return s;
    }

    /** One line, a byte at a time (nothing past it: the TLS handshake follows right after). */
    private static String line(InputStream in) throws IOException {
        StringBuilder b = new StringBuilder();
        for (int c; (c = in.read()) != -1 && c != '\n' && b.length() < 4096; ) b.append((char) c);
        return b.toString();
    }
}
