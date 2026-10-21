package net.netplay.core;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/**
 * The controller packet the host reads (PROTOCOL.md): 16 bytes little endian, ver u8 (=1), seq u8, buttons u32,
 * lx ly rx ry i16, lt rt u8. Button bit n = W3C standard-gamepad button n (12-15 = d-pad up/down/left/right).
 * Sticks: -32767..32767, up = negative. Plus when to send: on every change, and a heartbeat every 50 ms.
 */
public final class Pad {
    public static final long HEARTBEAT_MS = 50;
    public static final float DEAD = 0.1f;

    public int buttons;
    public final float[] ax = new float[4];     // -1..1
    public int lt, rt;                           // 0..255

    private int seq;
    private long lastAt = -1_000_000;
    private String last = "";
    public int sent;

    public static float dead(float v) { return Math.abs(v) < DEAD ? 0f : v; }

    /** Trigger buttons (bits 6, 7) count as fully pressed triggers, like the desktop player. */
    public static byte[] pack(int buttons, float[] ax, int lt, int rt, int seq) {
        if ((buttons & (1 << 6)) != 0) lt = 255;
        if ((buttons & (1 << 7)) != 0) rt = 255;
        ByteBuffer b = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN);
        b.put((byte) 1).put((byte) (seq & 255)).putInt(buttons);
        for (int i = 0; i < 4; i++) b.putShort((short) Math.max(-32767, Math.min(32767, Math.round(ax[i] * 32767))));
        b.put((byte) Math.max(0, Math.min(255, lt))).put((byte) Math.max(0, Math.min(255, rt)));
        return b.array();
    }

    /** The packet to send now (the state changed, or 50 ms passed), or null. */
    public byte[] next(long nowMs) {
        String sig = buttons + "," + dead(ax[0]) + "," + dead(ax[1]) + "," + dead(ax[2]) + "," + dead(ax[3]) + "," + lt + "," + rt;
        if (sig.equals(last) && nowMs - lastAt < HEARTBEAT_MS) return null;
        last = sig;
        lastAt = nowMs;
        seq = (seq + 1) & 255;
        sent++;
        float[] a = {dead(ax[0]), dead(ax[1]), dead(ax[2]), dead(ax[3])};
        return pack(buttons, a, lt, rt, seq);
    }
}
