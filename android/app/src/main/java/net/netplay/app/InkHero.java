package net.netplay.app;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.DashPathEffect;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PorterDuff;
import android.graphics.PorterDuffXfermode;
import android.graphics.Rect;
import android.graphics.RectF;
import android.view.MotionEvent;
import android.view.View;
import android.view.animation.DecelerateInterpolator;

import java.util.Random;

/**
 * The home picture: a game controller drawn as ink grain on paper, dissolving toward one corner, with four
 * heat-map squares laid over it. Each square is a live check (server, game, emulator, stream) joined to the
 * controller by a hairline. Square colour is the state: cool = not needed or unknown, green = ready, orange to
 * purple = needs your attention. While a search runs the squares drift through the spectrum and the hairlines flow.
 */
final class InkHero extends View {
    static final int UNKNOWN = 0, READY = 1, ATTENTION = 2;

    interface Listener { void onSquare(int index); }

    private static final float[][] SPOTS = {   // centre x, centre y, size (fractions of width)
            {0.19f, 0.25f, 0.17f}, {0.80f, 0.17f, 0.13f}, {0.78f, 0.66f, 0.21f}, {0.22f, 0.76f, 0.15f}};
    private static final float[] BASE = {0.04f, 0.30f, 0.86f};
    private static final int GRID = 36;

    private String[] names = {"Server", "Game", "Emulator", "Stream"};
    private String[] notes = {"", "", "", ""};
    private int[] states = {UNKNOWN, UNKNOWN, UNKNOWN, UNKNOWN};
    private Listener listener;

    private Bitmap art;                     // paper, grain, ink and the corner captions, drawn once per size
    private final Bitmap[] sq = new Bitmap[4];
    private final int[][] px = new int[4][GRID * GRID];
    private final float[][] noise = new float[4][GRID * GRID];
    private final int[] lut = new int[256];
    private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint nearest = new Paint();
    private final RectF[] hit = new RectF[4];

    private boolean searching;
    private float clock;                    // seconds, only advances while searching
    private float intro;                    // 0..1 hairlines drawing on at first show
    private ValueAnimator ticker, introAnim;
    private long last;

    InkHero(Context c) {
        super(c);
        for (int i = 0; i < 256; i++) lut[i] = Ink.spectrum(i / 255f);
        Random r = new Random(11);
        for (float[] n : noise) for (int k = 0; k < n.length; k++) n[k] = r.nextFloat();
        nearest.setFilterBitmap(false);
        for (int i = 0; i < 4; i++) { sq[i] = Bitmap.createBitmap(GRID, GRID, Bitmap.Config.ARGB_8888); hit[i] = new RectF(); }
        setFocusable(false);
    }

    void setListener(Listener l) { listener = l; }

    void setChecks(String[] n, String[] note, int[] st) {
        names = n; notes = note; states = st;
        StringBuilder sb = new StringBuilder("Setup status. ");
        for (int i = 0; i < 4; i++) sb.append(n[i]).append(": ").append(note[i]).append(". ");
        setContentDescription(sb.toString());
        paintSquares();
        invalidate();
    }

    void setSearching(boolean on) {
        searching = on;
        if (ticker != null) { ticker.cancel(); ticker = null; }
        if (on) {
            last = System.nanoTime();
            ticker = ValueAnimator.ofFloat(0f, 1f);
            ticker.setDuration(1000);
            ticker.setRepeatCount(ValueAnimator.INFINITE);
            ticker.addUpdateListener(a -> {
                long now = System.nanoTime();
                clock += (now - last) / 1e9f;
                last = now;
                paintSquares();
                invalidate();
            });
            ticker.start();
        } else {
            paintSquares();
            invalidate();
        }
    }

    @Override protected void onMeasure(int wSpec, int hSpec) {
        int w = MeasureSpec.getSize(wSpec);
        if (MeasureSpec.getMode(hSpec) == MeasureSpec.EXACTLY && MeasureSpec.getSize(hSpec) > 0) {
            setMeasuredDimension(w, MeasureSpec.getSize(hSpec));       // side column on a wide screen
            return;
        }
        // on a phone held upright: keep the picture from filling the whole screen
        int cap = Math.round(getResources().getDisplayMetrics().heightPixels * 0.42f);
        setMeasuredDimension(w, Math.min(Math.round(w * 0.86f), Math.max(cap, Ink.dp(getContext(), 220))));
    }

    @Override protected void onSizeChanged(int w, int h, int ow, int oh) {
        if (w <= 0 || h <= 0) return;
        art = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        drawArt(new Canvas(art), w, h);
        paintSquares();
        if (introAnim == null) {
            introAnim = ValueAnimator.ofFloat(0f, 1f);
            introAnim.setDuration(1100);
            introAnim.setStartDelay(150);
            introAnim.setInterpolator(new DecelerateInterpolator(1.6f));
            introAnim.addUpdateListener(a -> { intro = (float) a.getAnimatedValue(); invalidate(); });
            intro = 0f;
            introAnim.start();
        }
    }

    @Override protected void onDetachedFromWindow() {
        if (ticker != null) ticker.cancel();
        if (introAnim != null) introAnim.cancel();
        super.onDetachedFromWindow();
    }

    // ---------------------------------------------------------------- the ink picture (drawn once)
    private void drawArt(Canvas c, int w, int h) {
        c.drawColor(Ink.PAPER);
        Random rnd = new Random(5);
        Paint dot = new Paint();
        dot.setStyle(Paint.Style.FILL);
        float u = Ink.dp(getContext(), 1f);

        // paper grain
        for (int i = 0; i < w * h / 260; i++) {
            dot.setColor(rnd.nextBoolean() ? 0x30FFFFFF : 0x1E8A8880);
            float x = rnd.nextFloat() * w, y = rnd.nextFloat() * h;
            c.drawRect(x, y, x + u, y + u, dot);
        }

        // a soft mask of the controller: filled shape, details cut out, then blurred so the edge breaks up
        int mw = 90, mh = Math.max(40, Math.round(mw * (float) h / w));
        Bitmap mb = Bitmap.createBitmap(mw, mh, Bitmap.Config.ARGB_8888);
        Canvas mc = new Canvas(mb);
        float s = mw * 0.040f;                        // icon units -> mask pixels
        Matrix m = new Matrix();
        m.postTranslate(-12f, -12f);
        m.postScale(s, s);
        m.postRotate(-9f);
        m.postTranslate(mw * 0.54f, mh * 0.52f);
        Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
        fill.setColor(0xFFFFFFFF);
        Path body = pathOf(Icons.PAD[0]);
        body.transform(m);
        mc.drawPath(body, fill);
        Paint cut = new Paint(Paint.ANTI_ALIAS_FLAG);
        cut.setXfermode(new PorterDuffXfermode(PorterDuff.Mode.CLEAR));
        cut.setStyle(Paint.Style.STROKE);
        cut.setStrokeCap(Paint.Cap.ROUND);
        cut.setStrokeWidth(s * 0.9f);
        for (int i = 1; i <= 2; i++) {            // the d-pad cross
            Path d = pathOf(Icons.PAD[i]);
            d.transform(m);
            mc.drawPath(d, cut);
        }
        cut.setStyle(Paint.Style.FILL);
        for (int i = 3; i < Icons.PAD.length; i++) {   // the two face buttons, as round holes
            float[] pts = Icons.PAD[i];
            float bx = 0, by = 0;
            for (int k = 0; k + 1 < pts.length; k += 2) { bx += pts[k]; by += pts[k + 1]; }
            float[] ctr = {bx / (pts.length / 2), by / (pts.length / 2)};
            m.mapPoints(ctr);
            mc.drawCircle(ctr[0], ctr[1], s * 1.1f, cut);
        }
        float[] a = new float[mw * mh];
        int[] raw = new int[mw * mh];
        mb.getPixels(raw, 0, mw, 0, 0, mw, mh);
        for (int i = 0; i < raw.length; i++) a[i] = (raw[i] >>> 24) / 255f;
        a = blur(blur(a, mw, mh, 1), mw, mh, 1);

        // ink: random points kept in proportion to the mask, thinning toward the lower left
        Paint ink = new Paint(Paint.ANTI_ALIAS_FLAG);
        for (int i = 0; i < 80000; i++) {
            float x = rnd.nextFloat() * w, y = rnd.nextFloat() * h;
            float d = sample(a, mw, mh, x / w * mw, y / h * mh);
            float fade = clamp01(1.5f * (0.62f * x / w + 0.55f * (1f - y / h)) - 0.35f);
            float prob = d * (0.25f + 0.75f * fade);
            if (rnd.nextFloat() > prob * 2.6f) continue;
            float size = u * 1.5f * (0.8f + 1.2f * prob * rnd.nextFloat());
            int alpha = (int) (110 + 145 * clamp01(prob + 0.3f * rnd.nextFloat()));
            ink.setColor((alpha << 24) | 0x141414);
            c.drawCircle(x, y, size * 0.5f, ink);
        }
        // a few stray flecks outside the shape
        for (int i = 0; i < 260; i++) {
            float x = rnd.nextFloat() * w, y = rnd.nextFloat() * h;
            if (sample(a, mw, mh, x / w * mw, y / h * mh) > 0.18f) continue;
            if (rnd.nextFloat() > 0.35f) continue;
            ink.setColor(0xB0141414);
            c.drawCircle(x, y, u * (0.5f + rnd.nextFloat()), ink);
        }

        // corner captions
        Paint t = new Paint(Paint.ANTI_ALIAS_FLAG);
        t.setTypeface(Ink.mono(getContext(), false));
        t.setColor(Ink.INK);
        t.setTextSize(Ink.dp(getContext(), 13.5f));
        t.setLetterSpacing(0.06f);
        float pad = Ink.dp(getContext(), 20);
        c.drawText("PLAY TOGETHER,", pad, pad + t.getTextSize(), t);
        c.drawText("FROM ANYWHERE.", pad, pad + t.getTextSize() * 2.25f, t);
        String foot = "RETRO × NETPLAY";
        c.drawText(foot, w - pad - t.measureText(foot), h - pad * 0.8f, t);
    }

    private static Path pathOf(float[] pts) {
        Path p = new Path();
        for (int k = 0; k + 1 < pts.length; k += 2) {
            if (k == 0) p.moveTo(pts[k], pts[k + 1]); else p.lineTo(pts[k], pts[k + 1]);
        }
        p.close();
        return p;
    }

    private static float clamp01(float v) { return v < 0 ? 0 : Math.min(v, 1); }

    private static float[] blur(float[] src, int w, int h, int r) {
        float[] tmp = new float[src.length], out = new float[src.length];
        for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
            float sum = 0; int n = 0;
            for (int k = -r; k <= r; k++) { int xx = x + k; if (xx >= 0 && xx < w) { sum += src[y * w + xx]; n++; } }
            tmp[y * w + x] = sum / n;
        }
        for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
            float sum = 0; int n = 0;
            for (int k = -r; k <= r; k++) { int yy = y + k; if (yy >= 0 && yy < h) { sum += tmp[yy * w + x]; n++; } }
            out[y * w + x] = sum / n;
        }
        return out;
    }

    private static float sample(float[] a, int w, int h, float x, float y) {
        x = Math.max(0, Math.min(w - 1.001f, x)); y = Math.max(0, Math.min(h - 1.001f, y));
        int x0 = (int) x, y0 = (int) y;
        float fx = x - x0, fy = y - y0;
        float top = a[y0 * w + x0] * (1 - fx) + a[y0 * w + x0 + 1] * fx;
        float bot = a[(y0 + 1) * w + x0] * (1 - fx) + a[(y0 + 1) * w + x0 + 1] * fx;
        return top * (1 - fy) + bot * fy;
    }

    // ---------------------------------------------------------------- the squares (redrawn when state changes)
    private void paintSquares() {
        float ang = searching ? clock * 1.1f : 0.6f;
        float ca = (float) Math.cos(ang), sa = (float) Math.sin(ang);
        for (int i = 0; i < 4; i++) {
            float base = BASE[states[i]];
            if (searching) base += 0.16f * (float) Math.sin(clock * 2.2f + i * 1.7f);
            int[] out = px[i];
            for (int y = 0; y < GRID; y++) for (int x = 0; x < GRID; x++) {
                float g = ca * (x / (float) GRID - 0.5f) + sa * (y / (float) GRID - 0.5f);
                float t = base + 0.55f * g + (noise[i][y * GRID + x] - 0.5f) * 0.34f;
                int idx = Math.max(0, Math.min(255, (int) (t * 255f)));
                out[y * GRID + x] = lut[idx];
            }
            sq[i].setPixels(out, 0, GRID, 0, 0, GRID, GRID);
        }
    }

    @Override protected void onDraw(Canvas c) {
        int w = getWidth(), h = getHeight();
        if (art != null) c.drawBitmap(art, 0, 0, null);
        float core = Ink.dp(getContext(), 1f);
        float cx = w * 0.52f, cy = h * 0.50f;

        // hairlines first so the squares sit on top
        p.setStyle(Paint.Style.STROKE);
        p.setStrokeWidth(core);
        p.setColor(0xCCF4F3EF);
        for (int i = 0; i < 4; i++) {
            float sx = SPOTS[i][0] * w, sy = SPOTS[i][1] * h;
            float ex = sx + (cx - sx) * intro, ey = sy + (cy - sy) * intro;
            p.setPathEffect(searching ? new DashPathEffect(new float[]{core * 7, core * 6}, -clock * core * 26) : null);
            c.drawLine(sx, sy, ex, ey, p);
        }
        p.setPathEffect(null);

        Paint label = new Paint(Paint.ANTI_ALIAS_FLAG);
        label.setTypeface(Ink.mono(getContext(), false));
        label.setTextSize(Ink.dp(getContext(), 12f));
        Paint labelBold = new Paint(label);
        labelBold.setTypeface(Ink.mono(getContext(), true));
        for (int i = 0; i < 4; i++) {
            float size = SPOTS[i][2] * Math.min(w, h / 0.86f) * (0.6f + 0.4f * intro);
            float sx = SPOTS[i][0] * w, sy = SPOTS[i][1] * h;
            RectF r = hit[i];
            r.set(sx - size / 2, sy - size / 2, sx + size / 2, sy + size / 2);
            nearest.setAlpha((int) (255 * Math.min(1f, intro * 1.6f)));
            c.drawBitmap(sq[i], new Rect(0, 0, GRID, GRID), r, nearest);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(core);
            p.setColor(0xFFF4F3EF);
            c.drawRect(r, p);

            // caption chip beside the square, on the side with more room
            float pad = Ink.dp(getContext(), 6);
            String a = names[i].toUpperCase(), b = notes[i];
            float tw = Math.max(labelBold.measureText(a), label.measureText(b)) + pad * 2;
            float th = label.getTextSize() * 2.5f + pad;
            boolean right = sx < w * 0.5f;
            float left = right ? r.right + pad * 0.6f : r.left - pad * 0.6f - tw;
            float top = r.top;
            if (left + tw > w - pad) left = w - pad - tw;
            if (left < pad) left = pad;
            p.setStyle(Paint.Style.FILL);
            p.setColor(0xF0E4E3DF);
            c.drawRect(left, top, left + tw, top + th, p);
            labelBold.setColor(Ink.INK);
            label.setColor(Ink.INK_SOFT);
            c.drawText(a, left + pad, top + pad + label.getTextSize() * 0.9f, labelBold);
            c.drawText(b, left + pad, top + pad + label.getTextSize() * 2.1f, label);
            hit[i].union(left, top, left + tw, top + th);
        }
    }

    @Override public boolean onTouchEvent(MotionEvent e) {
        if (e.getAction() == MotionEvent.ACTION_UP && listener != null) {
            for (int i = 0; i < 4; i++) if (hit[i].contains(e.getX(), e.getY())) { performClick(); listener.onSquare(i); return true; }
        }
        return e.getAction() == MotionEvent.ACTION_DOWN;
    }

    @Override public boolean performClick() { return super.performClick(); }
}
