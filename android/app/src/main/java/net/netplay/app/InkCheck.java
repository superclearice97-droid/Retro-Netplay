package net.netplay.app;

import android.content.Context;
import android.graphics.Color;
import android.view.Gravity;
import android.view.View;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/** A square tick box whose tick draws itself in when you switch it on. */
final class InkCheck extends LinearLayout {
    private final IconView tick;
    private boolean on;

    InkCheck(Context c, String text, boolean initial) {
        super(c);
        setOrientation(HORIZONTAL);
        setGravity(Gravity.CENTER_VERTICAL);
        setFocusable(true);
        setClickable(true);
        setMinimumHeight(Ink.dp(c, 48));
        setBackground(Ink.surface(c, false));
        setPadding(Ink.dp(c, 8), Ink.dp(c, 6), Ink.dp(c, 8), Ink.dp(c, 6));
        FrameLayout box = new FrameLayout(c);
        box.setBackground(Ink.box(Color.TRANSPARENT, Ink.INK, 2, c));
        tick = new IconView(c, Icons.CHECK, Ink.INK);
        tick.setStrokeUnits(2.4f);
        box.addView(tick, new FrameLayout.LayoutParams(Ink.dp(c, 24), Ink.dp(c, 24), Gravity.CENTER));
        addView(box, new LayoutParams(Ink.dp(c, 26), Ink.dp(c, 26)));
        TextView t = Ink.text(c, text, 14, false, Ink.INK);
        LayoutParams lp = new LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f);
        lp.leftMargin = Ink.dp(c, 12);
        addView(t, lp);
        on = initial;
        tick.setVisibility(on ? View.VISIBLE : View.INVISIBLE);
        setOnClickListener(v -> set(!on, true));
    }

    boolean isOn() { return on; }

    void set(boolean value, boolean animate) {
        on = value;
        tick.setVisibility(on ? View.VISIBLE : View.INVISIBLE);
        if (on && animate) tick.play();
    }

    @Override public void onInitializeAccessibilityNodeInfo(AccessibilityNodeInfo info) {
        super.onInitializeAccessibilityNodeInfo(info);
        info.setCheckable(true);
        info.setChecked(on);
    }
}
