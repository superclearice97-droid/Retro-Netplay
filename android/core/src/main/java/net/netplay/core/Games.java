package net.netplay.core;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/** Systems and the game key. The key MUST match the desktop app (see PROTOCOL.md). */
public final class Games {
    private Games() {}

    public static final class Sys {
        public final String id, label, emu, core;
        public Sys(String id, String label, String emu, String core) {
            this.id = id; this.label = label; this.emu = emu; this.core = core;
        }
        /** Emulator netplay we can start on Android (RetroArch cores). */
        public boolean hasNative() { return "retroarch".equals(emu); }
        @Override public String toString() { return label; }
    }

    public static final Map<String, Sys> SYSTEMS = new LinkedHashMap<>();
    static {
        add(new Sys("gba", "Game Boy Advance", "retroarch", "mgba"));
        add(new Sys("ps1", "PlayStation 1", "retroarch", "pcsx_rearmed"));
        add(new Sys("dreamcast", "Dreamcast", "retroarch", "flycast"));
        add(new Sys("gamecube", "GameCube / Wii", "dolphin", null));
        add(new Sys("ps2", "PlayStation 2", "pcsx2", null));
        add(new Sys("ps3", "PlayStation 3", "rpcs3", null));
    }

    private static void add(Sys s) { SYSTEMS.put(s.id, s); }

    public static String slug(String name) {
        String lower = name.toLowerCase(Locale.ROOT);
        StringBuilder b = new StringBuilder();
        boolean dash = false;
        for (int k = 0; k < lower.length(); k++) {
            char c = lower.charAt(k);
            if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) {
                if (dash && b.length() > 0) b.append('-');
                dash = false;
                b.append(c);
            } else dash = true;
        }
        return b.toString();
    }

    public static String key(String systemId, String name) { return systemId + ":" + slug(name); }
}
