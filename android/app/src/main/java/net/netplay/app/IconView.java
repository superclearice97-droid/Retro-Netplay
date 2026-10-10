package net.netplay.app;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.PathMeasure;
import android.view.View;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.LinearInterpolator;

/**
 * Draws one of the Icons as strokes and can draw it on, so a tap or a state change is something you watch happen.
 * play(): draw on once. loop(true): draw on, then wipe off, again and again (use for work in progress).
 */
final class IconView extends View {
    private float[][] icon;
    private Path[] paths = new Path[0];
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path seg = new Path();
    private final PathMeasure pm = new PathMeasure();
    private float progress = 1f, erase = 0f;
    private ValueAnimator anim;
    private float strokeUnits = 1.75f;

    IconView(Context c, float[][] icon, int color) {
        super(c);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setStrokeJoin(Paint.Join.ROUND);
        paint.setColor(color);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        setIcon(icon, false);
    }

    void setColor(int color) { paint.setColor(color); invalidate(); }

    void setStrokeUnits(float u) { strokeUnits = u; invalidate(); }

    void setIcon(float[][] strokes, boolean animate) {
        stop();
        icon = strokes;
        rebuild();
        if (animate) play(); else { progress = 1f; erase = 0f; invalidate(); }
    }

    private void rebuild() {
        if (icon == null) { paths = new Path[0]; return; }
        float s = Math.min(getWidth(), getHeight()) / 24f;
        float ox = (getWidth() - 24f * s) / 2f, oy = (getHeight() - 24f * s) / 2f;
        paths = new Path[icon.length];
        for (int i = 0; i < icon.length; i++) {
            Path p = new Path();
            float[] pts = icon[i];
            for (int k = 0; k + 1 < pts.length; k += 2) {
                float x = ox + pts[k] * s, y = oy + pts[k + 1] * s;
                if (k == 0) p.moveTo(x, y); else p.lineTo(x, y);
            }
            paths[i] = p;
        }
    }

    @Override protected void onSizeChanged(int w, int h, int ow, int oh) { rebuild(); }

    private void stop() { if (anim != null) { anim.cancel(); anim = null; } }

    /** Draw the strokes on, one after another. */
    void play() {
        stop();
        erase = 0f;
        anim = ValueAnimator.ofFloat(0f, 1f);
        anim.setDuration(520);
        anim.setInterpolator(new DecelerateInterpolator(1.4f));
        anim.addUpdateListener(a -> { progress = (float) a.getAnimatedValue(); invalidate(); });
        anim.start();
    }

    /** Endless draw-on and wipe-off. */
    void loop(boolean on) {
        stop();
        if (!on) { progress = 1f; erase = 0f; invalidate(); return; }
        anim = ValueAnimator.ofFloat(0f, 2f);
        anim.setDuration(1500);
        anim.setInterpolator(new LinearInterpolator());
        anim.setRepeatCount(ValueAnimator.INFINITE);
        anim.addUpdateListener(a -> {
            float t = (float) a.getAnimatedValue();
            if (t < 1f) { progress = t; erase = 0f; } else { progress = 1f; erase = t - 1f; }
            invalidate();
        });
        anim.start();
    }

    @Override protected void onDetachedFromWindow() { stop(); super.onDetachedFromWindow(); }

    @Override protected void onDraw(Canvas c) {
        float s = Math.min(getWidth(), getHeight()) / 24f;
        paint.setStrokeWidth(strokeUnits * s);
        int n = paths.length;
        for (int i = 0; i < n; i++) {
            float start = n > 1 ? 0.5f * i / (n - 1) : 0f;
            float lp = Math.max(0f, Math.min(1f, (progress - start) / (n > 1 ? 0.5f : 1f)));
            if (lp <= 0f) continue;
            pm.setPath(paths[i], false);
            float len = pm.getLength();
            float from = len * erase * lp, to = len * lp;
            if (to - from < 0.5f && lp < 1f) continue;
            if (lp >= 1f && erase <= 0f) { c.drawPath(paths[i], paint); continue; }
            seg.reset();
            pm.getSegment(from, to, seg, true);
            c.drawPath(seg, paint);
        }
    }
}
