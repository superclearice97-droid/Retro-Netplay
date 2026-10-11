package net.netplay.app;

import android.accessibilityservice.AccessibilityService;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.PixelFormat;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * The controller shortcut: hold Start + Select for 3 seconds, in any game, to join (or leave) the queue.
 *
 * Android only lets an app see buttons pressed in another app through an accessibility service, so that is
 * what this is. It is off until you switch it on. It looks at the Start and Select buttons only, never stores
 * or sends any key press, never reads the screen, and always passes every button on to the game unchanged.
 */
public class HotkeyService extends AccessibilityService {
    static final long HOLD_MS = 3000;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean start, select, armed;
    private final Runnable fire = () -> {
        armed = false;
        if (this.card != null && this.cardYes != null) { Runnable y = this.cardYes; this.removeCard(); y.run(); return; }   // accept the open card
        Hub.get(this).hotkey();
    };

    // ---------------------------------------------------------------- corner card (join requests)
    private static volatile HotkeyService instance;
    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final String[] CORNERS = {"top-right", "bottom-right", "bottom-left", "top-left"};
    private View card;
    private String cardKey;
    private Runnable cardYes, cardNo, countdown;

    @Override protected void onServiceConnected() { super.onServiceConnected(); instance = this; }

    @Override public void onDestroy() {
        MAIN.post(this::removeCard);
        instance = null;
        super.onDestroy();
    }

    /** A card in a screen corner, above the game: title, text, Accept / Decline and a button to move it.
     *  Shown only while the controller shortcut is switched on (the app falls back to a notification). */
    static void showCard(Context c, String title, String text, Runnable yes, Runnable no, String key) {
        MAIN.post(() -> { HotkeyService s = instance; if (s != null) s.addCard(title, text, yes, no, key); });
    }

    static void closeCard(String key) {
        MAIN.post(() -> { HotkeyService s = instance; if (s != null && key != null && key.equals(s.cardKey)) s.removeCard(); });
    }

    private SharedPreferences prefs() { return getSharedPreferences("nm", MODE_PRIVATE); }

    private void addCard(String title, String text, Runnable yes, Runnable no, String key) {
        removeCard();
        final int[] left = {55};
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = Ink.dp(this, 14);
        box.setPadding(pad, pad, pad, pad);
        box.setBackground(Ink.box(Ink.PAPER, Ink.INK, 2, this));
        LinearLayout top = new LinearLayout(this);
        top.setOrientation(LinearLayout.HORIZONTAL);
        top.setGravity(Gravity.CENTER_VERTICAL);
        TextView t = Ink.caption(this, title);
        top.addView(t, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));
        TextView move = Ink.text(this, "\u21F2 move", 13, false, Ink.INK_SOFT);
        move.setPadding(Ink.dp(this, 10), Ink.dp(this, 6), 0, Ink.dp(this, 6));
        move.setOnClickListener(v -> moveCard());
        top.addView(move);
        box.addView(top);
        TextView body = Ink.text(this, text, 15, true, Ink.INK);
        LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        bp.topMargin = Ink.dp(this, 6);
        box.addView(body, bp);
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        InkButton ok = new InkButton(this, Icons.CHECK, "Accept", true);
        ok.onPress(() -> { removeCard(); yes.run(); });
        InkButton nope = new InkButton(this, Icons.CLOSE, "Decline", false);
        nope.onPress(() -> { removeCard(); no.run(); });
        LinearLayout.LayoutParams h1 = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        LinearLayout.LayoutParams h2 = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        h2.leftMargin = Ink.dp(this, 8);
        row.addView(ok, h1);
        row.addView(nope, h2);
        LinearLayout.LayoutParams rp = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        rp.topMargin = Ink.dp(this, 10);
        box.addView(row, rp);
        final TextView hint = Ink.text(this, "", 12, false, Ink.INK_SOFT);
        LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        hp.topMargin = Ink.dp(this, 8);
        box.addView(hint, hp);
        card = box;
        cardKey = key;
        cardYes = yes;
        cardNo = no;
        try {
            ((WindowManager) getSystemService(WINDOW_SERVICE)).addView(box, layout());
        } catch (RuntimeException e) {
            card = null;
            return;
        }
        countdown = new Runnable() {
            @Override public void run() {
                if (card != box) return;
                hint.setText("Hold Start + Select to accept \u00b7 closes in " + left[0] + " s");
                if (left[0]-- <= 0) { removeCard(); no.run(); return; }
                MAIN.postDelayed(this, 1000);
            }
        };
        countdown.run();
    }

    private WindowManager.LayoutParams layout() {
        String corner = prefs().getString("card_corner", "top-right");
        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                Math.min(Ink.dp(this, 380), getResources().getDisplayMetrics().widthPixels - Ink.dp(this, 32)),
                WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        lp.gravity = (corner.startsWith("top") ? Gravity.TOP : Gravity.BOTTOM) | (corner.endsWith("right") ? Gravity.END : Gravity.START);
        lp.x = Ink.dp(this, 20);
        lp.y = Ink.dp(this, 20);
        return lp;
    }

    private void moveCard() {
        String cur = prefs().getString("card_corner", "top-right");
        int i = 0;
        for (int k = 0; k < CORNERS.length; k++) if (CORNERS[k].equals(cur)) i = k;
        prefs().edit().putString("card_corner", CORNERS[(i + 1) % CORNERS.length]).apply();
        if (card != null) {
            try { ((WindowManager) getSystemService(WINDOW_SERVICE)).updateViewLayout(card, layout()); } catch (RuntimeException ignored) { }
        }
    }

    private void removeCard() {
        View c = card;
        card = null;
        cardKey = null;
        cardYes = cardNo = null;
        if (countdown != null) MAIN.removeCallbacks(countdown);
        if (c != null) {
            try { ((WindowManager) getSystemService(WINDOW_SERVICE)).removeView(c); } catch (RuntimeException ignored) { }
        }
    }

    static boolean isOn(Context c) {
        try {
            String list = Settings.Secure.getString(c.getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
            if (list == null) return false;
            String me = new ComponentName(c, HotkeyService.class).flattenToString();
            String shortMe = new ComponentName(c, HotkeyService.class).flattenToShortString();
            for (String s : list.split(":")) if (s.equalsIgnoreCase(me) || s.equalsIgnoreCase(shortMe)) return true;
        } catch (RuntimeException ignored) { }
        return false;
    }

    @Override protected boolean onKeyEvent(KeyEvent e) {
        int code = e.getKeyCode();
        boolean down = e.getAction() == KeyEvent.ACTION_DOWN;
        if (code == KeyEvent.KEYCODE_BUTTON_START) start = down;
        else if (code == KeyEvent.KEYCODE_BUTTON_SELECT) select = down;
        else return false;
        if (start && select) {
            if (!armed) {
                armed = true;
                handler.postDelayed(fire, HOLD_MS);
            }
        } else if (armed) {
            armed = false;
            handler.removeCallbacks(fire);
        }
        return false;   // never swallow the press: the game still gets it
    }

    @Override public void onAccessibilityEvent(AccessibilityEvent event) { }

    @Override public void onInterrupt() {
        start = select = armed = false;
        handler.removeCallbacks(fire);
    }
}
