package net.netplay.core;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;

/**
 * RetroArch's network commands (Settings > Network > Network Commands, UDP port 55355 on this device only).
 * Used to ask which game is running, start hosting netplay, and show a message on the game screen.
 * RetroArch has no command to join someone else's game, so the joining player still picks it in RetroArch's menu.
 */
public final class RetroCmd {
    private RetroCmd() { }

    public static volatile int port = 55355;

    public static final class Status {
        public final boolean playing, paused;
        public final String system, basename, crc;
        Status(boolean playing, boolean paused, String system, String basename, String crc) {
            this.playing = playing; this.paused = paused; this.system = system; this.basename = basename; this.crc = crc;
        }
        public boolean hasGame() { return playing || paused; }
    }

    /** Fire and forget. */
    public static void send(String cmd) {
        try (DatagramSocket s = new DatagramSocket()) {
            byte[] b = cmd.getBytes(StandardCharsets.UTF_8);
            s.send(new DatagramPacket(b, b.length, InetAddress.getByName("127.0.0.1"), port));
        } catch (IOException | RuntimeException ignored) { }
    }

    /** Send and wait for one reply line, or null if RetroArch didn't answer (not running, or Network Commands off). */
    public static String ask(String cmd, int timeoutMs) {
        try (DatagramSocket s = new DatagramSocket()) {
            s.setSoTimeout(timeoutMs);
            byte[] b = cmd.getBytes(StandardCharsets.UTF_8);
            s.send(new DatagramPacket(b, b.length, InetAddress.getByName("127.0.0.1"), port));
            byte[] buf = new byte[4096];
            DatagramPacket p = new DatagramPacket(buf, buf.length);
            s.receive(p);
            return new String(p.getData(), 0, p.getLength(), StandardCharsets.UTF_8).trim();
        } catch (SocketTimeoutException e) {
            return null;
        } catch (IOException | RuntimeException e) {
            return null;
        }
    }

    public static boolean available() {
        String v = ask("VERSION", 700);
        return v != null && !v.isEmpty();
    }

    /** "GET_STATUS PLAYING game_boy_advance,Sonic Battle (USA),crc32=1234abcd" -> Status. Null if no answer. */
    public static Status status() {
        return parseStatus(ask("GET_STATUS", 700));
    }

    public static Status parseStatus(String r) {
        if (r == null || !r.startsWith("GET_STATUS ")) return null;
        String rest = r.substring("GET_STATUS ".length()).trim();
        int sp = rest.indexOf(' ');
        String state = sp < 0 ? rest : rest.substring(0, sp);
        boolean playing = state.equals("PLAYING"), paused = state.equals("PAUSED");
        if (!playing && !paused) return new Status(false, false, "", "", "");
        String info = sp < 0 ? "" : rest.substring(sp + 1);
        String crc = "";
        int c = info.lastIndexOf(",crc32=");
        if (c >= 0) { crc = info.substring(c + 7); info = info.substring(0, c); }
        int comma = info.indexOf(',');
        String system = comma < 0 ? "" : info.substring(0, comma);
        String base = comma < 0 ? info : info.substring(comma + 1);
        return new Status(playing, paused, system, base, crc);
    }

    /** A short line on RetroArch's screen. */
    public static void showMsg(String text) {
        send("SHOW_MSG " + text.replace('\n', ' '));
    }

    /** Starts (or stops) hosting netplay for the game that is running. */
    public static void hostToggle() {
        send("NETPLAY_HOST_TOGGLE");
    }

    /** Switch between playing and watching in a netplay session. */
    public static void watchToggle() {
        send("NETPLAY_GAME_WATCH");
    }

    /** RetroArch's netplay nickname, so the other player can find you in its host list. "" if unknown. */
    public static String nickname() {
        String r = ask("GET_CONFIG_PARAM netplay_nickname", 700);
        String pre = "GET_CONFIG_PARAM netplay_nickname";
        if (r == null || !r.startsWith(pre)) return "";
        String v = r.substring(pre.length()).trim();
        return v.length() > 32 ? v.substring(0, 32) : v;
    }
}
