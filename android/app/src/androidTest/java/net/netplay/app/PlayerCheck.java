package net.netplay.app;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

import net.netplay.core.Json;

/**
 * (automatic tests) Plays a real stream in the app's player and reports what it saw. Run by
 * desktop/tests/test_android_player.py in an emulator, against the real host on the computer running it:
 *   am instrument -w -e host 10.0.2.2 -e port P -e key K -e seconds 8 net.netplay.matchmaker.test/net.netplay.app.PlayerCheck
 * Prints INSTRUMENTATION_RESULT: result={"frames":..., "inputs":..., "slot":..., "decoder":..., "ended":...}.
 * With -e mode card it opens a game's title card instead, with test pictures it draws and the clip in -e video,
 * and reports what the card showed (desktop/tests/test_android_card.py).
 */
public class PlayerCheck extends Instrumentation {
    private Bundle args;

    @Override public void onCreate(Bundle arguments) {
        args = arguments;
        start();
    }

    @Override public void onStart() {
        if ("card".equals(args.getString("mode"))) { card(); return; }
        Bundle out = new Bundle();
        try {
            String link = PlayerActivity.link(args.getString("host"), Integer.parseInt(args.getString("port")), args.getString("key"), "Test game", true);
            Intent i = new Intent(getTargetContext(), PlayerActivity.class).setData(Uri.parse(link))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    .putExtra("test_seconds", Long.parseLong(args.getString("seconds", "8")))
                    .putExtra("test_input", !"0".equals(args.getString("input", "1")));
            Activity a = startActivitySync(i);
            long end = System.currentTimeMillis() + 120000;
            while (PlayerActivity.lastResult == null && System.currentTimeMillis() < end) Thread.sleep(200);
            out.putString("result", Json.stringify(PlayerActivity.lastResult != null ? PlayerActivity.lastResult
                    : java.util.Collections.singletonMap("ended", "(no result in 120 s)")));
            Thread.sleep(2000);                       // (the goodbye reaches the host)
            a.finish();
        } catch (Exception e) {
            out.putString("result", Json.stringify(java.util.Collections.singletonMap("ended", "(test crashed: " + e + ")")));
        }
        finish(Activity.RESULT_OK, out);
    }

    private void card() {
        Bundle out = new Bundle();
        try {
            java.io.File dir = getTargetContext().getExternalFilesDir(null);
            String box = picture(new java.io.File(dir, "box.png"), 300, 420, 0xFF1E50C8, 0xFFF0C820);
            String s1 = picture(new java.io.File(dir, "shot1.png"), 240, 160, 0xFF58A0E8, 0xFF3C8C28);
            String s2 = picture(new java.io.File(dir, "shot2.png"), 240, 160, 0xFFE87858, 0xFF283C8C);
            java.util.Map<String, Object> artMap = new java.util.LinkedHashMap<>();
            artMap.put("name", "Test Game: The Card");
            artMap.put("desc", "A description written for this test: two players race through test levels & collect test stars.");
            artMap.put("year", "2003"); artMap.put("developer", "Test Developer"); artMap.put("genre", "Racing");
            artMap.put("players", "1-2"); artMap.put("rating", "0.70"); artMap.put("box", box);
            artMap.put("screens", java.util.Arrays.asList(s1, s2));
            artMap.put("video", args.getString("video", ""));
            java.util.Map<String, Object> g = new java.util.LinkedHashMap<>();
            g.put("name", "Test Game"); g.put("system", "gba"); g.put("rom", ""); g.put("art", artMap);
            Intent i = GameActivity.open(getTargetContext(), g).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            GameActivity.lastResult = null;
            Activity a = startActivitySync(i);
            Thread.sleep(Long.parseLong(args.getString("seconds", "8")) * 1000);
            out.putString("result", Json.stringify(GameActivity.lastResult != null ? GameActivity.lastResult
                    : java.util.Collections.singletonMap("built", false)));
            a.finish();
        } catch (Exception e) {
            out.putString("result", Json.stringify(java.util.Collections.singletonMap("error", "(test crashed: " + e + ")")));
        }
        finish(Activity.RESULT_OK, out);
    }

    /** A made-up picture (two colours and a few shapes): no real game art in the tests. */
    private static String picture(java.io.File f, int w, int h, int a, int b) throws java.io.IOException {
        android.graphics.Bitmap bm = android.graphics.Bitmap.createBitmap(w, h, android.graphics.Bitmap.Config.ARGB_8888);
        android.graphics.Canvas c = new android.graphics.Canvas(bm);
        c.drawColor(a);
        android.graphics.Paint p = new android.graphics.Paint();
        p.setColor(b);
        for (int k = 0; k < 6; k++) c.drawCircle(w * (0.15f + 0.14f * k), h * (0.3f + 0.08f * (k % 3)), w * 0.09f, p);
        c.drawRect(0, h * 0.75f, w, h, p);
        try (java.io.FileOutputStream o = new java.io.FileOutputStream(f)) { bm.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, o); }
        return f.getAbsolutePath();
    }
}
