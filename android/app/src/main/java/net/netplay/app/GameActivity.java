package net.netplay.app;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Outline;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewOutlineProvider;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.VideoView;

import net.netplay.core.Json;

import java.io.File;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A game's title card: a moving background themed on the game (MeshView), its gameplay clip (looping, muted) or
 * screenshots (SlidesView) on an old-TV screen, the box art, the details from its gamelist, and Play. It stays open
 * while the app looks for an opponent. The art comes from the player's own folders (see Setup.attachArt).
 */
public class GameActivity extends Activity implements Hub.Ui {
    private static final int TEXT = 0xFFF7F5EF, SOFT = 0xFFC9C6BD, ACCENT = 0xFF66F2B0, DARK = 0xFF111111;
    /** (automatic tests) what the last card showed. */
    static volatile Map<String, Object> lastResult;

    private Hub hub;
    private Map<String, Object> game, art;
    private String key;
    private FrameLayout root;
    private MeshView mesh;
    private SlidesView slides;
    private VideoView video;
    private TextView status, goText;
    private IconView goIcon;
    private LinearLayout go;
    private boolean wasQueued, videoPlaying, built, boxShown;
    private int mode = -1;

    static Intent open(Context c, Map<String, Object> g) {
        return new Intent(c, GameActivity.class).putExtra("game", Json.stringify(g));
    }

    @SuppressWarnings("unchecked")
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        hub = Hub.get(this);
        try { game = Json.parseObject(getIntent().getStringExtra("game")); } catch (RuntimeException e) { game = null; }
        if (game == null || game.get("name") == null || game.get("system") == null) { finish(); return; }
        key = Hub.key(game);
        art = game.get("art") instanceof Map ? (Map<String, Object>) game.get("art") : new LinkedHashMap<>();
        root = new FrameLayout(this);
        root.setBackgroundColor(0xFF000000);
        mesh = new MeshView(this, MeshView.theme((String) game.get("system")), 3f);
        root.addView(mesh, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        Sfx.play(this, "open");
        new Thread(() -> {      // pictures are read off the main thread, then the card is built
            final Bitmap box = image(s("box"), 900);
            final List<Bitmap> shots = new ArrayList<>();
            Object sc = art.get("screens");
            if (sc instanceof List) for (Object o : (List<Object>) sc) {
                if (shots.size() >= 4) break;
                Bitmap bm = image(o instanceof String ? (String) o : "", 1280);
                if (bm != null) shots.add(bm);
            }
            if (shots.isEmpty()) { Bitmap t = image(s("title"), 1280); if (t != null) shots.add(t); }
            runOnUiThread(() -> { if (!isFinishing()) build(box, shots); });
        }).start();
    }

    private String s(String k) { Object v = art.get(k); return v instanceof String ? (String) v : ""; }

    private static Uri uri(String where) {
        return where.startsWith("content:") || where.startsWith("file:") ? Uri.parse(where) : Uri.fromFile(new File(where));
    }

    private Bitmap image(String where, int max) {
        if (where.isEmpty()) return null;
        try {
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inJustDecodeBounds = true;
            try (InputStream in = getContentResolver().openInputStream(uri(where))) { BitmapFactory.decodeStream(in, null, o); }
            int sample = 1;
            while (o.outWidth / (sample * 2) >= max || o.outHeight / (sample * 2) >= max) sample *= 2;
            BitmapFactory.Options d = new BitmapFactory.Options();
            d.inSampleSize = sample;
            try (InputStream in = getContentResolver().openInputStream(uri(where))) { return BitmapFactory.decodeStream(in, null, d); }
        } catch (Exception | OutOfMemoryError e) { return null; }      // a missing or broken picture is just left out
    }

    private int dp(float v) { return Ink.dp(this, v); }

    private void build(Bitmap box, List<Bitmap> shots) {
        boxShown = box != null;
        if (box != null) {
            root.removeView(mesh);
            mesh = new MeshView(this, MeshView.paletteFrom(box, MeshView.theme((String) game.get("system"))), 3f + Math.abs(key.hashCode() % 17));
            root.addView(mesh, 0, new FrameLayout.LayoutParams(-1, -1));
        }
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(18), dp(22), dp(18), dp(18));

        // the gameplay window
        FrameLayout screen = new FrameLayout(this);
        screen.setBackgroundColor(0xFF000000);
        screen.setOutlineProvider(new ViewOutlineProvider() {
            @Override public void getOutline(View v, Outline o) { o.setRoundRect(0, 0, v.getWidth(), v.getHeight(), dp(14)); }
        });
        screen.setClipToOutline(true);
        slides = new SlidesView(this, shots, false);
        screen.addView(slides, new FrameLayout.LayoutParams(-1, -1));
        String clip = s("video");
        if (!clip.isEmpty()) {
            video = new VideoView(this);
            FrameLayout.LayoutParams vp = new FrameLayout.LayoutParams(-1, -1, Gravity.CENTER);
            screen.addView(video, vp);
            video.setOnPreparedListener(mp -> { mp.setLooping(true); mp.setVolume(0f, 0f); videoPlaying = true; video.start(); report(); });
            video.setOnErrorListener((mp, what, extra) -> { screen.removeView(video); video = null; report(); return true; });
            video.setVideoURI(uri(clip));
            screen.addView(new SlidesView(this, null, true), new FrameLayout.LayoutParams(-1, -1));   // scanlines over the clip
        } else if (shots.isEmpty()) {
            LinearLayout none = new LinearLayout(this);
            none.setOrientation(LinearLayout.VERTICAL);
            none.setGravity(Gravity.CENTER);
            none.setPadding(dp(20), dp(10), dp(20), dp(10));
            IconView film = new IconView(this, Icons.FILM, SOFT);
            none.addView(film, new LinearLayout.LayoutParams(dp(44), dp(44)));
            TextView t = Ink.text(this, "No screenshots yet. Run Skraper on your game folders (on a computer) and copy them "
                    + "back, and the game's art and details appear here.", 12.5f, false, SOFT);
            t.setGravity(Gravity.CENTER);
            none.addView(t);
            screen.addView(none, new FrameLayout.LayoutParams(-1, -1));
        }
        page.addView(screen, new LinearLayout.LayoutParams(-1, 0));
        screen.post(() -> { ViewGroup.LayoutParams lp = screen.getLayoutParams(); lp.height = (int) (screen.getWidth() * 0.62f); screen.setLayoutParams(lp); });

        // box art + title and details
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, dp(18), 0, 0);
        View boxView;
        if (box != null) {
            ImageView iv = new ImageView(this);
            iv.setImageBitmap(box);
            iv.setScaleType(ImageView.ScaleType.FIT_START);
            iv.setAdjustViewBounds(true);
            iv.setContentDescription("Box art");
            iv.setElevation(dp(8));
            boxView = iv;
        } else {
            LinearLayout ph = new LinearLayout(this);
            ph.setGravity(Gravity.CENTER);
            ph.setOrientation(LinearLayout.VERTICAL);
            ph.setBackground(round(0x33000000, 0x66FFFFFF));
            ph.addView(new IconView(this, Icons.IMAGE, SOFT), new LinearLayout.LayoutParams(dp(34), dp(34)));
            ph.addView(Ink.text(this, "No box art", 11, false, SOFT));
            ph.setMinimumHeight(dp(150));
            boxView = ph;
        }
        row.addView(boxView, new LinearLayout.LayoutParams(dp(108), ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout col = new LinearLayout(this);
        col.setOrientation(LinearLayout.VERTICAL);
        col.setPadding(dp(16), 0, 0, 0);
        String name = s("name").isEmpty() ? (String) game.get("name") : s("name");
        TextView title = Ink.text(this, name, 21, true, TEXT);
        title.setShadowLayer(4, 0, 2, 0xFF000000);
        col.addView(title);
        List<IconView> icons = new ArrayList<>();
        meta(col, icons, Icons.GAMEPAD, Hub.sys(game).label);
        if (!s("year").isEmpty()) meta(col, icons, Icons.CALENDAR, s("year"));
        String who = s("developer").isEmpty() ? s("publisher") : s("developer");
        if (!who.isEmpty()) meta(col, icons, Icons.BUILDING, who);
        if (!s("players").isEmpty()) meta(col, icons, Icons.USERS, s("players") + ("1".equals(s("players")) ? " player" : " players"));
        if (!s("genre").isEmpty()) meta(col, icons, Icons.TAG, s("genre"));
        if (!s("rating").isEmpty()) {
            LinearLayout stars = new LinearLayout(this);
            stars.setPadding(0, dp(6), 0, 0);
            float r = 0f;
            try { r = Float.parseFloat(s("rating")) * 5f; } catch (NumberFormatException ignored) { }
            for (int i = 0; i < 5; i++) {
                IconView st = new IconView(this, Icons.STAR, i + 0.5f < r ? ACCENT : 0x66FFFFFF);
                LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(dp(18), dp(18));
                sp.rightMargin = dp(3);
                stars.addView(st, sp);
            }
            stars.setContentDescription(String.format(java.util.Locale.ROOT, "Rated %.1f out of 5", r));
            col.addView(stars);
        }
        row.addView(col, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        page.addView(row);
        for (int i = 0; i < icons.size(); i++) { IconView ic = icons.get(i); root.postDelayed(ic::move, 300 + 120L * i); }

        if (!s("desc").isEmpty()) {
            TextView d = Ink.text(this, s("desc"), 13.5f, false, SOFT);
            d.setLineSpacing(0, 1.15f);
            d.setPadding(0, dp(16), 0, 0);
            d.setShadowLayer(3, 0, 1, 0xFF000000);
            page.addView(d);
        }
        ScrollView scroll = new ScrollView(this);
        scroll.addView(page);

        // status and Play at the bottom
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.VERTICAL);
        bar.setPadding(dp(18), dp(8), dp(18), dp(18));
        status = Ink.text(this, "", 13.5f, true, ACCENT);
        status.setShadowLayer(3, 0, 1, 0xFF000000);
        bar.addView(status);
        go = new LinearLayout(this);
        go.setGravity(Gravity.CENTER);
        go.setMinimumHeight(dp(56));
        go.setFocusable(true);
        go.setClickable(true);
        go.setBackground(round(TEXT, 0));
        goIcon = new IconView(this, Icons.GO, DARK);
        go.addView(goIcon, new LinearLayout.LayoutParams(dp(24), dp(24)));
        goText = Ink.text(this, "PLAY", 15, true, DARK);
        goText.setLetterSpacing(0.08f);
        goText.setPadding(dp(12), 0, 0, 0);
        go.addView(goText);
        go.setOnClickListener(v -> press());
        LinearLayout.LayoutParams gp = new LinearLayout.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT);
        gp.topMargin = dp(8);
        bar.addView(go, gp);

        LinearLayout all = new LinearLayout(this);
        all.setOrientation(LinearLayout.VERTICAL);
        all.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1f));
        all.addView(bar);
        root.addView(all, new FrameLayout.LayoutParams(-1, -1));
        built = true;
        mode = -1;
        hubChanged();
        go.requestFocus();
        report();
    }

    private GradientDrawable round(int fill, int stroke) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(fill);
        g.setCornerRadius(dp(10));
        if (stroke != 0) g.setStroke(dp(1), stroke);
        return g;
    }

    private void meta(LinearLayout col, List<IconView> icons, float[][] icon, String text) {
        LinearLayout r = new LinearLayout(this);
        r.setGravity(Gravity.CENTER_VERTICAL);
        r.setPadding(0, dp(6), 0, 0);
        IconView iv = new IconView(this, icon, ACCENT);
        r.addView(iv, new LinearLayout.LayoutParams(dp(18), dp(18)));
        TextView t = Ink.text(this, text, 13, false, SOFT);
        t.setPadding(dp(8), 0, 0, 0);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        r.addView(t);
        col.addView(r);
        icons.add(iv);
    }

    private void press() {
        boolean q = hub.state == Hub.State.QUEUE && key.equals(hub.queueKey);
        if (q) { Sfx.play(this, "back"); hub.leave(); return; }
        Sfx.play(this, "play");
        goIcon.move();
        hub.play(this, game, getSharedPreferences("nm", MODE_PRIVATE).getBoolean("auto_queue", true));
    }

    @Override protected void onStart() {
        super.onStart();
        if (hub == null) return;
        hub.addUi(this);
        if (mesh != null) mesh.setRunning(true);
        if (slides != null) slides.setRunning(true);
        if (video != null && videoPlaying) video.start();
        hubChanged();
    }

    @Override protected void onStop() {
        if (hub != null) hub.removeUi(this);
        if (mesh != null) mesh.setRunning(false);      // nothing moves (or uses the battery) while you're in the game
        if (slides != null) slides.setRunning(false);
        if (video != null) video.pause();
        super.onStop();
    }

    @Override public void onBackPressed() {
        Sfx.play(this, "back");
        super.onBackPressed();
    }

    @Override public void hubChanged() {
        if (!built) return;
        boolean q = hub.state == Hub.State.QUEUE && key.equals(hub.queueKey);
        boolean playing = key.equals(hub.playingKey);
        if (wasQueued && !q && playing) Sfx.play(this, "found");
        wasQueued = q;
        status.setText(q ? (hub.queueWith.isEmpty() ? "Looking for an opponent..." : "Connecting you with " + hub.queueWith + "...")
                : playing ? (hub.playingWith.isEmpty() ? "Playing. Hold Start + Select for 3 seconds to look for an opponent." : "Playing with " + hub.playingWith)
                : "");
        status.setVisibility(status.getText().length() == 0 ? View.GONE : View.VISIBLE);
        int m = q ? 1 : 0;
        if (m != mode) {
            mode = m;
            goIcon.setIcon(q ? Icons.SEEK : Icons.GO, false);
            goText.setText(q ? "LEAVE QUEUE" : (Hub.sys(game).hasNative() ? "PLAY" : "JOIN THE QUEUE"));
            go.setContentDescription(goText.getText());
            if (q) seekLoop();
        }
        report();
    }

    private void seekLoop() {
        if (mode == 1 && !isFinishing()) { goIcon.move(); root.postDelayed(this::seekLoop, 1600); }
    }

    @Override public void incomingCall(Hub.Call c) {
        Ink.sheet(this, c.name, Ink.text(this, c.name + " " + c.what(hub) + ".", 15, false, Ink.INK),
                "Allow", () -> hub.answer(c.id, true), "Deny", () -> hub.answer(c.id, false)).show();
    }

    @Override public void say(String message) {
        if (status != null && !message.isEmpty()) { status.setText(message); status.setVisibility(View.VISIBLE); }
    }

    private void report() {
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("built", built);
        r.put("box", boxShown);
        r.put("slides", slides != null ? slides.count() : 0);
        r.put("video", videoPlaying);
        r.put("button", goText != null ? goText.getText().toString() : "");
        r.put("status", status != null ? status.getText().toString() : "");
        r.put("frames", mesh != null ? mesh.frames : 0);
        lastResult = r;
    }
}
