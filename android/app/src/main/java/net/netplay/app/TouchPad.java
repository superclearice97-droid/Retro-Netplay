package net.netplay.app;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.MotionEvent;
import android.view.View;

/**
 * On-screen buttons over the stream for players without a controller: d-pad on the left, A B X Y on the right,
 * L and R at the top corners, Select and Start in the middle. Several fingers at once work. Bits are the W3C
 * standard-gamepad numbers the host reads (A=0 B=1 X=2 Y=3 L=4 R=5 Select=8 Start=9, d-pad 12-15).
 */
class TouchPad extends View {
    interface Listener { void onButtons(int bits); }

    private static final String[] NAMES = {"A", "B", "X", "Y", "L", "R", "", "", "SELECT", "START", "", "", "▲", "▼", "◀", "▶"};
    private final RectF[] areas = new RectF[16];
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG), line = new Paint(Paint.ANTI_ALIAS_FLAG),
            label = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Listener listener;
    private int bits;

    TouchPad(Context c, Listener l) {
        super(c);
        listener = l;
        line.setStyle(Paint.Style.STROKE);
        line.setStrokeWidth(Ink.dp(c, 1.5f));
        line.setColor(0xAAFFFFFF);
        label.setColor(0xDDFFFFFF);
        label.setTextAlign(Paint.Align.CENTER);
        label.setTypeface(Ink.mono(c, true));
    }

    @Override protected void onSizeChanged(int w, int h, int ow, int oh) {
        float u = Math.min(w, h) / 7f;                    // one button
        float bottom = h - u * 0.6f;
        // d-pad: a plus sign, bottom left
        float cx = u * 2.1f, cy = bottom - u * 1.5f;
        areas[12] = square(cx, cy - u, u);
        areas[13] = square(cx, cy + u, u);
        areas[14] = square(cx - u, cy, u);
        areas[15] = square(cx + u, cy, u);
        // face buttons: a diamond, bottom right (Xbox layout: A bottom, B right, X left, Y top)
        cx = w - u * 2.1f;
        areas[0] = square(cx, cy + u, u);
        areas[1] = square(cx + u, cy, u);
        areas[2] = square(cx - u, cy, u);
        areas[3] = square(cx, cy - u, u);
        // shoulders at the top corners, Select / Start at the bottom middle
        areas[4] = new RectF(u * 0.5f, u * 0.5f, u * 2.5f, u * 1.3f);
        areas[5] = new RectF(w - u * 2.5f, u * 0.5f, w - u * 0.5f, u * 1.3f);
        areas[8] = new RectF(w / 2f - u * 2.1f, bottom - u * 0.7f, w / 2f - u * 0.3f, bottom);
        areas[9] = new RectF(w / 2f + u * 0.3f, bottom - u * 0.7f, w / 2f + u * 2.1f, bottom);
        label.setTextSize(u * 0.3f);
    }

    private static RectF square(float cx, float cy, float u) {
        float r = u * 0.48f;
        return new RectF(cx - r, cy - r, cx + r, cy + r);
    }

    @Override protected void onDraw(Canvas c) {
        for (int i = 0; i < areas.length; i++) {
            RectF r = areas[i];
            if (r == null) continue;
            boolean down = (bits & (1 << i)) != 0;
            fill.setColor(down ? 0x88FFFFFF : 0x33000000);
            float round = i < 4 ? r.width() / 2 : Ink.dp(getContext(), 8);
            c.drawRoundRect(r, round, round, fill);
            c.drawRoundRect(r, round, round, line);
            c.drawText(NAMES[i], r.centerX(), r.centerY() + label.getTextSize() * 0.35f, label);
        }
    }

    @Override public boolean onTouchEvent(MotionEvent e) {
        int now = 0;
        int action = e.getActionMasked();
        for (int p = 0; p < e.getPointerCount(); p++) {
            boolean lifted = (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_POINTER_UP) && p == e.getActionIndex();
            if (lifted || action == MotionEvent.ACTION_CANCEL) continue;
            now |= hit(e.getX(p), e.getY(p));
        }
        if (now != bits) {
            bits = now;
            listener.onButtons(bits);
            invalidate();
        }
        return true;
    }

    /** Which buttons a finger at (x, y) presses; a finger between two d-pad arrows presses both (diagonals). */
    private int hit(float x, float y) {
        int b = 0;
        float slop = Ink.dp(getContext(), 10);
        for (int i = 0; i < areas.length; i++) {
            RectF r = areas[i];
            if (r != null && x >= r.left - slop && x <= r.right + slop && y >= r.top - slop && y <= r.bottom + slop) b |= 1 << i;
        }
        return b;
    }

    void release() {
        if (bits != 0) {
            bits = 0;
            listener.onButtons(0);
            invalidate();
        }
    }
}
