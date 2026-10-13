package net.netplay.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;

/** Notifications: the ongoing queue card, "matched!", and friends' calls with Accept / Decline. */
final class Notify {
    private Notify() { }

    static final int ONGOING = 1, MATCH = 2;
    static final String CH_QUEUE = "queue", CH_MATCH = "match", CH_CALL = "calls";

    static void channels(Context c) {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null) return;
        nm.createNotificationChannel(new NotificationChannel(CH_QUEUE, "Queue and playing", NotificationManager.IMPORTANCE_LOW));
        NotificationChannel m = new NotificationChannel(CH_MATCH, "Match found", NotificationManager.IMPORTANCE_HIGH);
        m.enableVibration(true);
        nm.createNotificationChannel(m);
        NotificationChannel k = new NotificationChannel(CH_CALL, "Friends asking to play", NotificationManager.IMPORTANCE_HIGH);
        k.enableVibration(true);
        nm.createNotificationChannel(k);
    }

    @SuppressWarnings("deprecation")
    static Notification.Builder builder(Context c, String channel, boolean urgent) {
        channels(c);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(c, channel) : new Notification.Builder(c);
        b.setSmallIcon(android.R.drawable.ic_media_play);
        b.setColor(Ink.INK);
        if (Build.VERSION.SDK_INT < 26) b.setPriority(urgent ? Notification.PRIORITY_HIGH : Notification.PRIORITY_LOW);
        if (urgent && Build.VERSION.SDK_INT < 26) b.setDefaults(Notification.DEFAULT_VIBRATE);
        return b;
    }

    static int flags() {
        return PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0);
    }

    static PendingIntent openApp(Context c) {
        Intent i = new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(c, 0, i, flags());
    }

    static PendingIntent action(Context c, String what, String callId, int req) {
        Intent i = new Intent(c, ActionReceiver.class).setAction(what);
        if (callId != null) i.putExtra("call", callId);
        return PendingIntent.getBroadcast(c, req, i, flags());
    }

    @SuppressWarnings("deprecation")
    static Notification ongoing(Context c, Hub h) {
        String title, text;
        Notification.Builder b = builder(c, CH_QUEUE, false);
        if (h.state == Hub.State.QUEUE) {
            title = "In the queue: " + h.queueName;
            text = h.queueWith.isEmpty() ? "Hold Start + Select for 3 seconds to leave." : "Connecting you with " + h.queueWith + "...";
            b.setUsesChronometer(true).setWhen(h.queueSince);
            b.addAction(new Notification.Action.Builder(null, "Leave queue", action(c, ActionReceiver.LEAVE, null, 1)).build());
        } else if (!h.playingKey.isEmpty()) {
            title = "Playing " + h.gameTitle(h.playingKey) + (h.playingWith.isEmpty() ? "" : " with " + h.playingWith);
            text = h.hasPresence() ? "Friends can see you're playing. Hold Start + Select for 3 seconds to find an opponent."
                    : "Hold Start + Select for 3 seconds to find an opponent.";
        } else {
            title = "Retro-Netplay";
            text = h.message.isEmpty() ? "Ready." : h.message;
        }
        b.addAction(new Notification.Action.Builder(null, "Stop", action(c, ActionReceiver.STOP, null, 2)).build());
        return b.setContentTitle(title).setContentText(text).setStyle(new Notification.BigTextStyle().bigText(text))
                .setOngoing(true).setOnlyAlertOnce(true).setContentIntent(openApp(c)).build();
    }

    static void matched(Context c, String title, String text, String url) {
        try {
            NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
            PendingIntent tap = openApp(c);
            if (!url.isEmpty()) {
                Intent v = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                tap = PendingIntent.getActivity(c, 7, v, flags());
            }
            nm.notify(MATCH, builder(c, CH_MATCH, true).setContentTitle(title).setContentText(text)
                    .setStyle(new Notification.BigTextStyle().bigText(text)).setAutoCancel(true).setContentIntent(tap).build());
        } catch (RuntimeException ignored) { }
    }

    static void info(Context c, String title, String text) {
        try {
            NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
            nm.notify(3, builder(c, CH_CALL, true).setContentTitle(title).setContentText(text)
                    .setStyle(new Notification.BigTextStyle().bigText(text)).setAutoCancel(true).setContentIntent(openApp(c)).build());
        } catch (RuntimeException ignored) { }
    }

    static int requestNotifId(String id) { return 70000 + (id.hashCode() & 0xFFFF); }

    /** A friend request, with Accept / Decline right on the notification. */
    static void request(Context c, String id, String title, String text) {
        try {
            NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
            int n = requestNotifId(id);
            Notification.Builder b = builder(c, CH_CALL, true).setContentTitle(title).setContentText(text)
                    .setStyle(new Notification.BigTextStyle().bigText(text)).setAutoCancel(true)
                    .setContentIntent(PendingIntent.getActivity(c, n, new Intent(c, FriendsActivity.class), flags()));
            b.addAction(new Notification.Action.Builder(null, "Accept", requestAction(c, ActionReceiver.REQ_YES, id, n)).build());
            b.addAction(new Notification.Action.Builder(null, "Decline", requestAction(c, ActionReceiver.REQ_NO, id, n + 1)).build());
            nm.notify(n, b.build());
        } catch (RuntimeException ignored) { }
    }

    static PendingIntent requestAction(Context c, String what, String id, int req) {
        Intent i = new Intent(c, ActionReceiver.class).setAction(what).putExtra("friend", id);
        return PendingIntent.getBroadcast(c, req, i, flags());
    }

    static void cancelRequest(Context c, String id) {
        try { ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(requestNotifId(id)); }
        catch (RuntimeException ignored) { }
    }

    static int callNotifId(String callId) { return 100 + (callId.hashCode() & 0xFFFF); }

    static void call(Context c, Hub.Call call, String text) {
        try {
            NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
            int id = callNotifId(call.id);
            Notification.Builder b = builder(c, CH_CALL, true).setContentTitle(call.name).setContentText(text)
                    .setAutoCancel(true).setContentIntent(openApp(c));
            b.addAction(new Notification.Action.Builder(null, "Allow", action(c, ActionReceiver.ACCEPT, call.id, id)).build());
            b.addAction(new Notification.Action.Builder(null, "Deny", action(c, ActionReceiver.DECLINE, call.id, id + 50000)).build());
            if (Build.VERSION.SDK_INT >= 21) b.setCategory(Notification.CATEGORY_CALL);
            nm.notify(id, b.build());
        } catch (RuntimeException ignored) { }
    }

    static void cancelCall(Context c, String callId) {
        try {
            ((NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE)).cancel(callNotifId(callId));
        } catch (RuntimeException ignored) { }
    }
}
