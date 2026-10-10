package net.netplay.app;

import android.content.Context;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Rectangular button: hairline outline (or solid ink), an icon that draws itself on press, and a mono label. */
final class InkButton extends LinearLayout {
    private final IconView icon;
    private final TextView label;
    private final boolean filled;

    InkButton(Context c, float[][] strokes, String text, boolean filled) {
        super(c);
        this.filled = filled;
        setOrientation(HORIZONTAL);
        setGravity(Gravity.CENTER);
        setFocusable(true);
        setClickable(true);
        setMinimumHeight(Ink.dp(c, 52));
        setPadding(Ink.dp(c, 16), Ink.dp(c, 8), Ink.dp(c, 18), Ink.dp(c, 8));
        setBackground(Ink.surface(c, filled));
        int fg = filled ? Ink.PAPER : Ink.INK;
        icon = new IconView(c, strokes, fg);
        if (strokes != null) addView(icon, new LayoutParams(Ink.dp(c, 22), Ink.dp(c, 22)));
        label = Ink.text(c, text.toUpperCase(), 13.5f, true, fg);
        label.setLetterSpacing(0.06f);
        LayoutParams lp = new LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT);
        lp.leftMargin = strokes != null ? Ink.dp(c, 10) : 0;
        addView(label, lp);
    }

    void onPress(Runnable r) {
        setOnClickListener(v -> { icon.play(); if (r != null) r.run(); });
    }

    /** Swap icon and label together (for example search becomes cancel). */
    void set(float[][] strokes, String text) {
        icon.setIcon(strokes, true);
        label.setText(text.toUpperCase());
    }

    IconView icon() { return icon; }

    boolean isFilled() { return filled; }
}
