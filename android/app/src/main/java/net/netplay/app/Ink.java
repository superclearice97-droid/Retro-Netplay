package net.netplay.app;

import android.app.Dialog;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * Look and feel: warm grey paper, near-black ink, a monospace voice, and one spectrum of colour that only
 * ever means something (cool = unknown, green = ready, orange to purple = needs attention).
 */
final class Ink {
    private Ink() { }

    static final int PAPER = 0xFFE4E3DF;
    static final int PAPER_DEEP = 0xFFD8D7D2;
    static final int INK = 0xFF141414;
    static final int INK_SOFT = 0xFF4A4A48;
    static final int HAIR = 0x99141414;

    // sky -> mint -> lime -> orange -> red -> purple
    private static final int[] STOPS = {0xFF7FD0FF, 0xFF66F2B0, 0xFFE8F04A, 0xFFFF9331, 0xFFD2362E, 0xFF7A2B8F};

    /** Colour for t in 0..1 along the spectrum. */
    static int spectrum(float t) {
        t = Math.max(0f, Math.min(1f, t)) * (STOPS.length - 1);
        int i = Math.min((int) t, STOPS.length - 2);
        float f = t - i;
        int a = STOPS[i], b = STOPS[i + 1];
        return Color.rgb(
                (int) (Color.red(a) + (Color.red(b) - Color.red(a)) * f),
                (int) (Color.green(a) + (Color.green(b) - Color.green(a)) * f),
                (int) (Color.blue(a) + (Color.blue(b) - Color.blue(a)) * f));
    }

    private static Typeface regular, bold;

    static Typeface mono(Context c, boolean isBold) {
        try {
            if (regular == null) regular = Typeface.createFromAsset(c.getAssets(), "DejaVuSansMono.ttf");
            if (bold == null) bold = Typeface.createFromAsset(c.getAssets(), "DejaVuSansMono-Bold.ttf");
        } catch (RuntimeException e) {
            return isBold ? Typeface.create(Typeface.MONOSPACE, Typeface.BOLD) : Typeface.MONOSPACE;
        }
        return isBold ? bold : regular;
    }

    static int dp(Context c, float v) { return Math.round(v * c.getResources().getDisplayMetrics().density); }

    static TextView text(Context c, String s, float sp, boolean isBold, int color) {
        TextView t = new TextView(c);
        t.setText(s);
        t.setTextSize(sp);
        t.setTypeface(mono(c, isBold));
        t.setTextColor(color);
        t.setLineSpacing(0f, 1.18f);
        return t;
    }

    /** Small caption in the corner-label style: spaced, capitals. */
    static TextView caption(Context c, String s) {
        TextView t = text(c, s.toUpperCase(), 12.5f, false, INK);
        t.setLetterSpacing(0.08f);
        return t;
    }

    static GradientDrawable box(int fill, int stroke, int strokeDp, Context c) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        if (strokeDp > 0) g.setStroke(dp(c, strokeDp), stroke);
        return g;
    }

    /** Flat rectangle with a hairline edge, a thicker edge while focused, and a ripple on touch. */
    static android.graphics.drawable.Drawable surface(Context c, boolean filled) {
        int fill = filled ? INK : Color.TRANSPARENT;
        StateListDrawable s = new StateListDrawable();
        s.addState(new int[]{android.R.attr.state_focused}, box(fill, INK, 3, c));
        s.addState(new int[]{}, box(fill, INK, 1, c));
        int ripple = filled ? 0x44FFFFFF : 0x22141414;
        return new RippleDrawable(ColorStateList.valueOf(ripple), s, null);
    }

    /** Text field with only a hairline underneath. */
    static EditText field(Context c, String hint, String value) {
        EditText e = new EditText(c);
        e.setHint(hint);
        e.setText(value);
        e.setTypeface(mono(c, false));
        e.setTextSize(15);
        e.setTextColor(INK);
        e.setHintTextColor(0xFF77776F);
        e.setSingleLine(true);
        e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        e.setPadding(dp(c, 12), dp(c, 12), dp(c, 12), dp(c, 12));
        e.setBackground(surface(c, false));
        return e;
    }

    /** A modal sheet in the app's own style: title, content, then buttons along the bottom. */
    static Dialog sheet(Context c, String title, View content, String yes, Runnable onYes, String no) {
        final Dialog d = new Dialog(c);
        d.requestWindowFeature(Window.FEATURE_NO_TITLE);
        LinearLayout col = new LinearLayout(c);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(c, 20), dp(c, 20), dp(c, 20), dp(c, 16));
        col.setBackground(box(PAPER, INK, 2, c));
        TextView t = text(c, title, 17, true, INK);
        col.addView(t);
        if (content != null) {
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            lp.topMargin = dp(c, 14);
            col.addView(content, lp);
        }
        LinearLayout row = new LinearLayout(c);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.END);
        LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        rp.topMargin = dp(c, 18);
        if (no != null) {
            InkButton b = new InkButton(c, null, no, false);
            b.onPress(d::dismiss);
            row.addView(b);
        }
        if (yes != null) {
            InkButton b = new InkButton(c, Icons.CHECK, yes, true);
            b.onPress(() -> { d.dismiss(); if (onYes != null) onYes.run(); });
            LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            bp.leftMargin = dp(c, 10);
            row.addView(b, bp);
        }
        col.addView(row, rp);
        android.widget.ScrollView sv = new android.widget.ScrollView(c);
        sv.addView(col);
        d.setContentView(sv);
        Window w = d.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.TRANSPARENT));
            w.setLayout((int) (c.getResources().getDisplayMetrics().widthPixels * 0.92f), ViewGroup.LayoutParams.WRAP_CONTENT);
            w.setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        }
        return d;
    }
}
