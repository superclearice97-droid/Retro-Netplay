package net.netplay.app;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
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
 * move(): the icon's own motion (Lucide icons, ported from lucide-animated), or draw on when it has none.
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
    private float[] motion;
    private float motionT = -1f;

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

    private void stop() { if (anim != null) { anim.cancel(); anim = null; } motionT = -1f; }

    /** Play the icon's motion once (see Icons: seconds, parts, strokes, origin, keyframes). */
    void move() {
        float[] m = Icons.motion(icon);
        if (m == null) { play(); return; }
        stop();
        motion = m;
        progress = 1f; erase = 0f;
        anim = ValueAnimator.ofFloat(0f, 1f);
        anim.setDuration((long) (m[0] * 1000));
        anim.setInterpolator(new LinearInterpolator());
        anim.addUpdateListener(a -> { motionT = (float) a.getAnimatedValue(); invalidate(); });
        anim.addListener(new AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(Animator a) { motionT = -1f; invalidate(); }
        });
        anim.start();
    }

    /** -> per stroke {dx, dy, rotate, opacity, originX, originY} at time u (0-1), or null when it doesn't move. */
    private float[][] pose(float u) {
        float[][] out = new float[paths.length][];
        int i = 2;
        int parts = (int) motion[1];
        for (int p = 0; p < parts; p++) {
            int n = (int) motion[i++];
            int[] strokes = new int[n];
            for (int k = 0; k < n; k++) strokes[k] = (int) motion[i++];
            float ox = motion[i++], oy = motion[i++];
            int keys = (int) motion[i++];
            float[] v = {motion[i + 1], motion[i + 2], motion[i + 3], motion[i + 4]};
            for (int k = 0; k + 1 < keys; k++) {
                int a = i + k * 5, b = a + 5;
                if (u <= motion[b] || k + 2 == keys) {
                    float f = motion[b] == motion[a] ? 0f : Math.max(0f, Math.min(1f, (u - motion[a]) / (motion[b] - motion[a])));
                    f = f * f * (3 - 2 * f);
                    for (int c = 0; c < 4; c++) v[c] = motion[a + 1 + c] + (motion[b + 1 + c] - motion[a + 1 + c]) * f;
                    break;
                }
            }
            i += keys * 5;
            for (int s : strokes) if (s < out.length) out[s] = new float[]{v[0], v[1], v[2], v[3], ox, oy};
        }
        return out;
    }

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
        if (motionT >= 0f && motion != null) {
            float[][] pose = pose(motionT);
            float ox = (getWidth() - 24f * s) / 2f, oy = (getHeight() - 24f * s) / 2f;
            for (int i = 0; i < n; i++) {
                float[] p = pose[i];
                if (p == null) { c.drawPath(paths[i], paint); continue; }
                if (p[3] < 0.5f) continue;
                c.save();
                c.translate(p[0] * s, p[1] * s);
                c.rotate(p[2], ox + p[4] * s, oy + p[5] * s);
                c.drawPath(paths[i], paint);
                c.restore();
            }
            return;
        }
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
