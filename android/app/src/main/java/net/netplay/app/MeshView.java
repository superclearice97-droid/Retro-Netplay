package net.netplay.app;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.Shader;
import android.os.SystemClock;
import android.view.View;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The title card's moving background: a port of two effects from Shaders (github.com/shader-effects-inc/shaders,
 * MIT licence), the same maths as desktop/titlecard.py. MeshGradient: colour points on a golden-angle spiral drift on
 * slow Lissajous paths and each pixel blends their values by inverse distance, after a swirl about the centre.
 * CRTScreen: scanlines and a vignette. Drawn small and scaled up smoothly; darker at the top and bottom for text.
 */
final class MeshView extends View {
    static final int[][] THEMES = {
            {0xFF120A2E, 0xFF3A1D7A, 0xFF7B3FD0, 0xFFD45AB8, 0xFFFFD2F0},   // gba
            {0xFF0B0F1A, 0xFF1C2A44, 0xFF3D5A80, 0xFF98C1D9, 0xFFE0FBFC},   // ps1
            {0xFF02040F, 0xFF061A40, 0xFF0353A4, 0xFF00A6FB, 0xFFC6F1FF},   // ps2
            {0xFF050505, 0xFF1B1B22, 0xFF3A3D4A, 0xFF8A8FA3, 0xFFE6E8EE},   // ps3
            {0xFF1A0700, 0xFF5A1C00, 0xFFC2410C, 0xFFFB923C, 0xFFFFF1E0},   // dreamcast
            {0xFF0D0A24, 0xFF2A1F6B, 0xFF5B4BC4, 0xFFA99CF5, 0xFFEFEAFF},   // gamecube
    };
    private static final int LW = 48, LH = 27, COUNT = 5;
    private final float[] gx = new float[LW * LH], gy = new float[LW * LH], ti = new float[COUNT];
    private final int[] px = new int[LW * LH], ramp = new int[256];
    private final Bitmap small = Bitmap.createBitmap(LW, LH, Bitmap.Config.ARGB_8888);
    private final Paint smooth = new Paint(Paint.FILTER_BITMAP_FLAG), line = new Paint(), shade = new Paint(), scrim = new Paint();
    private final Rect dst = new Rect();
    private final float seed, power = 8f / (2f + 0.5f), aspect = LW / (float) LH;
    private final long start = SystemClock.uptimeMillis();
    private boolean running = true;
    int frames;

    MeshView(Context c, int[] stops, float seed) {
        super(c);
        this.seed = seed;
        for (int i = 0; i < COUNT; i++) ti[i] = frac(i * 0.6180339887f + hash(seed * 0.531f + 0.77f));
        for (int k = 0; k < 256; k++) ramp[k] = rampAt(stops, k / 255f);
        for (int y = 0; y < LH; y++) for (int x = 0; x < LW; x++) {
            float cx = (x + 0.5f) / LW * aspect - aspect / 2, cy = (y + 0.5f) / LH - 0.5f;
            float r = Math.min(1f, (float) Math.hypot(cx, cy));
            double a = -3.0 * 0.3 * (r * r * (3 - 2 * r));
            float ca = (float) Math.cos(a), sa = (float) Math.sin(a);
            gx[y * LW + x] = cx * ca - cy * sa + aspect / 2;
            gy[y * LW + x] = cx * sa + cy * ca + 0.5f;
        }
        line.setColor(0x24000000);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    }

    private static float frac(float v) { return v - (float) Math.floor(v); }
    private static float hash(float x) { return frac((float) (Math.sin(x * 12.9898 + 78.233) * 43758.5453)); }

    private static int rampAt(int[] s, float t) {
        int n = s.length - 1, i = Math.min((int) (t * n), n - 1);
        float f = t * n - i;
        int a = s[i], b = s[i + 1];
        return Color.rgb((int) (Color.red(a) + (Color.red(b) - Color.red(a)) * f), (int) (Color.green(a) + (Color.green(b) - Color.green(a)) * f),
                (int) (Color.blue(a) + (Color.blue(b) - Color.blue(a)) * f));
    }

    void setRunning(boolean on) { running = on; if (on) invalidate(); }

    private void render(float t) {
        float[] ax = new float[COUNT], ay = new float[COUNT];
        for (int i = 0; i < COUNT; i++) {
            double theta = i * 2.39996322972865 + hash(seed * 0.7311f + 1.618f) * Math.PI * 2;
            double r = Math.sqrt((i + 0.5) / COUNT);
            float jx = (hash(i * 3.77f + 0.917f) - 0.5f) * 0.26f, jy = (hash(seed * 1.093f + 2.236f + i) - 0.5f) * 0.26f;
            float ph1 = hash(i * 7.13f + 1.618f) * 6.2831853f, ph2 = hash(seed * 0.531f + 3.71f + i) * 6.2831853f;
            float w1 = 0.5f + hash(i * 5.417f + seed * 0.291f + 0.917f) * 0.7f;
            double dx = Math.sin(t * 0.11 * w1 + ph1), dy = Math.cos(t * 0.11 * w1 * 1.37 + ph2);
            ax[i] = (float) ((0.5 + Math.cos(theta) * r * 0.62 + jx + dx * 0.5 * 0.16) * aspect);
            ay[i] = (float) (0.5 + Math.sin(theta) * r * 0.62 + jy + dy * 0.5 * 0.16);
        }
        double half = power / 2;
        for (int p = 0; p < px.length; p++) {
            double num = 0, den = 0;
            for (int i = 0; i < COUNT; i++) {
                double d2 = Math.max((gx[p] - ax[i]) * (gx[p] - ax[i]) + (gy[p] - ay[i]) * (gy[p] - ay[i]), 1e-4);
                double w = Math.pow(d2, -half);
                num += w * ti[i];
                den += w;
            }
            px[p] = ramp[(int) (num / den * 255)];
        }
        small.setPixels(px, 0, LW, 0, 0, LW, LH);
    }

    @Override protected void onSizeChanged(int w, int h, int ow, int oh) {
        dst.set(0, 0, w, h);
        float max = (float) Math.hypot(w / 2f, h / 2f);
        shade.setShader(new RadialGradient(w / 2f, h / 2f, max, new int[]{0x00000000, 0x00000000, 0xB0000000},
                new float[]{0f, 0.55f, 1f}, Shader.TileMode.CLAMP));
        scrim.setShader(new LinearGradient(0, 0, 0, h, new int[]{0x66000000, 0x00000000, 0x00000000, 0x99000000},
                new float[]{0f, 0.25f, 0.6f, 1f}, Shader.TileMode.CLAMP));
    }

    @Override protected void onDraw(Canvas c) {
        render((SystemClock.uptimeMillis() - start) / 1000f * 2.2f);
        frames++;
        c.drawBitmap(small, null, dst, smooth);
        int w = getWidth(), h = getHeight(), step = Math.max(3, Ink.dp(getContext(), 3));
        for (int y = 0; y < h; y += step) c.drawLine(0, y, w, y, line);
        c.drawRect(dst, shade);
        c.drawRect(dst, scrim);
        if (running) postInvalidateDelayed(66);
    }

    /** Five colours from the box art, dark to light, darkened enough for light text on top. */
    static int[] paletteFrom(Bitmap art, int[] fallback) {
        try {
            Bitmap s = Bitmap.createScaledBitmap(art, 24, 24, true);
            Map<Integer, Integer> counts = new HashMap<>();
            for (int y = 0; y < 24; y++) for (int x = 0; x < 24; x++) {
                int p = s.getPixel(x, y);
                int q = Color.rgb(Color.red(p) & 0xE0, Color.green(p) & 0xE0, Color.blue(p) & 0xE0);
                counts.merge(q, 1, Integer::sum);
            }
            List<Map.Entry<Integer, Integer>> e = new ArrayList<>(counts.entrySet());
            e.sort((a, b) -> b.getValue() - a.getValue());
            List<Integer> cols = new ArrayList<>();
            for (int i = 0; i < Math.min(5, e.size()); i++) cols.add(e.get(i).getKey() | 0x101010);
            if (cols.size() < 3) return fallback;
            cols.sort((a, b) -> Float.compare(lum(a), lum(b)));
            while (cols.size() < 5) cols.add(cols.get(cols.size() - 1));
            float[] dark = {0.22f, 0.38f, 0.6f, 0.8f, 0.95f};
            int[] out = new int[5];
            for (int i = 0; i < 5; i++) {
                int c = cols.get(i);
                out[i] = Color.rgb((int) (Color.red(c) * dark[i]), (int) (Color.green(c) * dark[i]), (int) (Color.blue(c) * dark[i]));
            }
            return out;
        } catch (RuntimeException ex) { return fallback; }
    }

    private static float lum(int c) { return 0.2126f * Color.red(c) + 0.7152f * Color.green(c) + 0.0722f * Color.blue(c); }

    static int[] theme(String system) {
        String[] ids = {"gba", "ps1", "ps2", "ps3", "dreamcast", "gamecube"};
        for (int i = 0; i < ids.length; i++) if (ids[i].equals(system)) return THEMES[i];
        return THEMES[2];
    }
}
