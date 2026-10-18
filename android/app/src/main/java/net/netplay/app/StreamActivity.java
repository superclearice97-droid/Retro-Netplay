package net.netplay.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.util.Locale;

/**
 * Plays a friend's stream inside the app (no browser): shows the matchmaker's stream page full screen and passes
 * this device's own controller to it. Only ever opens the stream page of the matchmaker you're using.
 */
public class StreamActivity extends Activity {
    private WebView web;
    private final Handler main = new Handler(Looper.getMainLooper());
    private int buttons;
    private final float[] axes = new float[4];
    private int lt, rt;

    static void open(Context c, String url) {
        Intent i = new Intent(c, StreamActivity.class).setData(Uri.parse(url));
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        c.startActivity(i);
    }

    @SuppressLint("SetJavaScriptEnabled")
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);                    // the stream page is a small script (WebRTC + controller)
        s.setMediaPlaybackRequiresUserGesture(false);   // start the video without an extra tap
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                return !sameSite(r.getUrl());             // never wander off the stream page
            }
        });
        web.setFocusable(false);                          // buttons come to this screen, not to the page's text fields
        setContentView(web);
        load(getIntent());
        main.post(heartbeat);
    }

    @Override protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        load(i);
    }

    private Uri page;

    private void load(Intent i) {
        Uri u = i.getData();
        if (u == null || !"/play".equals(u.getPath()) || !("http".equals(u.getScheme()) || "https".equals(u.getScheme()))) {
            finish();
            return;
        }
        page = u;
        web.loadUrl(u.toString());
    }

    private boolean sameSite(Uri u) {
        return page != null && u != null && page.getHost() != null && page.getHost().equalsIgnoreCase(u.getHost())
                && page.getPort() == u.getPort();
    }

    @Override public void onWindowFocusChanged(boolean has) {
        super.onWindowFocusChanged(has);
        if (has) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    | View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        }
    }

    @Override protected void onDestroy() {
        main.removeCallbacks(heartbeat);
        if (web != null) {
            web.loadUrl("about:blank");                  // closes the video connection; the page says goodbye on unload
            web.destroy();
        }
        super.onDestroy();
    }

    // ---------------------------------------------------------------- controller
    /** Android key -> W3C standard gamepad button (the same numbers the stream sends to the host). */
    static int bit(int keyCode) {
        switch (keyCode) {
            case KeyEvent.KEYCODE_BUTTON_A: return 0;
            case KeyEvent.KEYCODE_BUTTON_B: return 1;
            case KeyEvent.KEYCODE_BUTTON_X: return 2;
            case KeyEvent.KEYCODE_BUTTON_Y: return 3;
            case KeyEvent.KEYCODE_BUTTON_L1: return 4;
            case KeyEvent.KEYCODE_BUTTON_R1: return 5;
            case KeyEvent.KEYCODE_BUTTON_L2: return 6;
            case KeyEvent.KEYCODE_BUTTON_R2: return 7;
            case KeyEvent.KEYCODE_BUTTON_SELECT: return 8;
            case KeyEvent.KEYCODE_BUTTON_START: return 9;
            case KeyEvent.KEYCODE_BUTTON_THUMBL: return 10;
            case KeyEvent.KEYCODE_BUTTON_THUMBR: return 11;
            case KeyEvent.KEYCODE_DPAD_UP: return 12;
            case KeyEvent.KEYCODE_DPAD_DOWN: return 13;
            case KeyEvent.KEYCODE_DPAD_LEFT: return 14;
            case KeyEvent.KEYCODE_DPAD_RIGHT: return 15;
            case KeyEvent.KEYCODE_BUTTON_MODE: return 16;
            default: return -1;
        }
    }

    @Override public boolean dispatchKeyEvent(KeyEvent e) {
        int n = bit(e.getKeyCode());
        boolean pad = (e.getSource() & (InputDevice.SOURCE_GAMEPAD | InputDevice.SOURCE_JOYSTICK | InputDevice.SOURCE_DPAD)) != 0;
        if (n < 0 || !pad) return super.dispatchKeyEvent(e);     // e.g. Back still leaves the stream
        if (e.getAction() == KeyEvent.ACTION_DOWN) buttons |= 1 << n;
        else if (e.getAction() == KeyEvent.ACTION_UP) buttons &= ~(1 << n);
        push();
        return true;
    }

    @Override public boolean dispatchGenericMotionEvent(MotionEvent e) {
        if ((e.getSource() & InputDevice.SOURCE_JOYSTICK) == 0 || e.getAction() != MotionEvent.ACTION_MOVE) {
            return super.dispatchGenericMotionEvent(e);
        }
        axes[0] = e.getAxisValue(MotionEvent.AXIS_X);
        axes[1] = e.getAxisValue(MotionEvent.AXIS_Y);
        axes[2] = e.getAxisValue(MotionEvent.AXIS_Z);
        axes[3] = e.getAxisValue(MotionEvent.AXIS_RZ);
        lt = trigger(Math.max(e.getAxisValue(MotionEvent.AXIS_LTRIGGER), e.getAxisValue(MotionEvent.AXIS_BRAKE)));
        rt = trigger(Math.max(e.getAxisValue(MotionEvent.AXIS_RTRIGGER), e.getAxisValue(MotionEvent.AXIS_GAS)));
        // some controllers send the d-pad as a hat (axes) instead of keys
        float hx = e.getAxisValue(MotionEvent.AXIS_HAT_X), hy = e.getAxisValue(MotionEvent.AXIS_HAT_Y);
        int hat = (hy < -0.5f ? 1 << 12 : 0) | (hy > 0.5f ? 1 << 13 : 0) | (hx < -0.5f ? 1 << 14 : 0) | (hx > 0.5f ? 1 << 15 : 0);
        buttons = (buttons & ~hatBits) | hat;
        hatBits = hat;
        push();
        return true;
    }

    private int hatBits;

    private static int trigger(float v) { return Math.max(0, Math.min(255, Math.round(v * 255))); }

    private void push() {
        if (web == null) return;
        web.evaluateJavascript(String.format(Locale.ROOT, "window.__nativePad&&__nativePad(%d,%.3f,%.3f,%.3f,%.3f,%d,%d)",
                buttons, axes[0], axes[1], axes[2], axes[3], lt, rt), null);
    }

    /** Buttons held still send no events: repeat the state so the page keeps it. */
    private final Runnable heartbeat = new Runnable() {
        @Override public void run() {
            if (buttons != 0 || lt != 0 || rt != 0 || axes[0] != 0 || axes[1] != 0 || axes[2] != 0 || axes[3] != 0) push();
            main.postDelayed(this, 500);
        }
    };
}
