package net.netplay.app;

import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/**
 * Keeps the app alive while you're in the queue or playing, so the match (or a friend's call) still reaches you
 * while RetroArch is on screen. Shows one ongoing notification with Leave / Stop buttons. Stops itself when idle.
 */
public class QueueService extends Service {
    private static volatile boolean running;

    static void start(Context c) {
        if (running) { refresh(c); return; }
        try {
            Intent i = new Intent(c, QueueService.class);
            if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i); else c.startService(i);
        } catch (RuntimeException e) {
            // Android can refuse to start it from the background; the queue still runs, just without the card.
            try {
                ((NotificationManager) c.getSystemService(NOTIFICATION_SERVICE)).notify(Notify.ONGOING, Notify.ongoing(c, Hub.get(c)));
            } catch (RuntimeException ignored) { }
        }
    }

    /** Update the card, or stop when there's nothing to keep alive for. */
    static void refresh(Context c) {
        Hub h = Hub.get(c);
        boolean needed = h.state == Hub.State.QUEUE || !h.playingKey.isEmpty();
        try {
            NotificationManager nm = (NotificationManager) c.getSystemService(NOTIFICATION_SERVICE);
            if (needed) {
                if (running) nm.notify(Notify.ONGOING, Notify.ongoing(c, h));
            } else {
                if (running) c.stopService(new Intent(c, QueueService.class));
                nm.cancel(Notify.ONGOING);
            }
        } catch (RuntimeException ignored) { }
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        running = true;
        Hub h = Hub.get(this);
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(Notify.ONGOING, Notify.ongoing(this, h), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            } else {
                startForeground(Notify.ONGOING, Notify.ongoing(this, h));
            }
        } catch (RuntimeException e) {
            running = false;
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!h.hasPresence()) h.restartPresence();
        return START_NOT_STICKY;
    }

    @Override public void onDestroy() {
        running = false;
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
