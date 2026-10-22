package net.netplay.app;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.os.SystemClock;
import android.view.View;

import java.util.ArrayList;
import java.util.List;

/**
 * The title card's gameplay window when there's no clip: screenshots slowly zooming and panning (Ken Burns) and
 * crossfading, behind old-TV scanlines. With scanlinesOnly it draws just the scanlines (over a playing clip).
 * The same timings as desktop/titlecard.py.
 */
final class SlidesView extends View {
    private static final float SHOW = 5f, FADE = 0.8f;
    private final List<Bitmap> shots = new ArrayList<>();
    private final Paint pic = new Paint(Paint.FILTER_BITMAP_FLAG), crisp = new Paint(), line = new Paint();
    private final Matrix m = new Matrix();
    private final long start = SystemClock.uptimeMillis();
    private final boolean scanlinesOnly;
    private boolean running = true;

    SlidesView(Context c, List<Bitmap> shots, boolean scanlinesOnly) {
        super(c);
        if (shots != null) this.shots.addAll(shots);
        this.scanlinesOnly = scanlinesOnly;
        line.setColor(0x33000000);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    }

    int count() { return shots.size(); }

    void setRunning(boolean on) { running = on; if (on) invalidate(); }

    @Override protected void onDraw(Canvas c) {
        int w = getWidth(), h = getHeight();
        if (!scanlinesOnly && !shots.isEmpty()) {
            float t = (SystemClock.uptimeMillis() - start) / 1000f;
            int n = shots.size(), i = (int) (t / SHOW) % n;
            float local = t % SHOW;
            draw(c, i, local, 255);
            if (n > 1 && local > SHOW - FADE) draw(c, (i + 1) % n, local - SHOW, (int) (255 * (local - (SHOW - FADE)) / FADE));
            if (running) postInvalidateDelayed(50);
        }
        int step = Math.max(3, Ink.dp(getContext(), 3));
        for (int y = 0; y < h; y += step) c.drawLine(0, y, w, y, line);
    }

    private void draw(Canvas c, int i, float local, int alpha) {
        Bitmap b = shots.get(i);
        int w = getWidth(), h = getHeight();
        float p = Math.max(0f, Math.min(1f, (local + FADE) / (SHOW + FADE)));
        float cover = Math.max(w / (float) b.getWidth(), h / (float) b.getHeight()) * (1.10f - 0.08f * p) * 1.02f;
        float sx = (i % 2 == 0 ? 1 : -1), sy = (i % 3 == 0 ? -1 : 1);
        float extraX = b.getWidth() * cover - w, extraY = b.getHeight() * cover - h;
        m.reset();
        m.postScale(cover, cover);
        m.postTranslate(-extraX * (0.5f + 0.45f * sx * (p - 0.5f)), -extraY * (0.5f + 0.45f * sy * (p - 0.5f)));
        Paint paint = b.getWidth() <= 640 ? crisp : pic;     // pixel art stays crisp
        paint.setAlpha(alpha);
        c.drawBitmap(b, m, paint);
    }
}
