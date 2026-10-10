package net.netplay.app;

import android.accessibilityservice.AccessibilityService;
import android.content.ComponentName;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityEvent;

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
        Hub.get(this).hotkey();
    };

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
