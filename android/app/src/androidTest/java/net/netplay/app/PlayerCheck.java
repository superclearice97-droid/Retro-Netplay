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
 */
public class PlayerCheck extends Instrumentation {
    private Bundle args;

    @Override public void onCreate(Bundle arguments) {
        args = arguments;
        start();
    }

    @Override public void onStart() {
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
}
