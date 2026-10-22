package net.netplay.app;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.SoundPool;

import java.util.HashMap;
import java.util.Map;

/** The interface sounds (made with earshot, see design/make_sounds.sh). Off when Settings > Interface sounds is off. */
final class Sfx {
    private static SoundPool pool;
    private static final Map<String, Integer> ids = new HashMap<>();

    private Sfx() { }

    static synchronized void play(Context c, String name) {
        if (!c.getSharedPreferences("nm", Context.MODE_PRIVATE).getBoolean("sounds", true)) return;
        try {
            if (pool == null) {
                pool = new SoundPool.Builder().setMaxStreams(3).setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()).build();
                pool.setOnLoadCompleteListener((p, id, status) -> { if (status == 0 && Boolean.TRUE.equals(waiting.remove(id))) p.play(id, 1f, 1f, 1, 0, 1f); });
                int[] raw = {R.raw.ui_move, R.raw.ui_open, R.raw.ui_play, R.raw.ui_found, R.raw.ui_back};
                String[] names = {"move", "open", "play", "found", "back"};
                for (int i = 0; i < raw.length; i++) ids.put(names[i], pool.load(c.getApplicationContext(), raw[i], 1));
            }
            Integer id = ids.get(name);
            if (id == null) return;
            if (pool.play(id, 1f, 1f, 1, 0, 1f) == 0) waiting.put(id, true);    // not loaded yet: play once it is
        } catch (RuntimeException ignored) { } // no sound is never a problem
    }

    private static final Map<Integer, Boolean> waiting = new HashMap<>();
}
