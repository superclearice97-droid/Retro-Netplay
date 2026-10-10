package net.netplay.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Buttons on the notifications. Not exported: only this app's own notifications can send these. */
public class ActionReceiver extends BroadcastReceiver {
    static final String LEAVE = "net.netplay.app.LEAVE";
    static final String STOP = "net.netplay.app.STOP";
    static final String WATCH = "net.netplay.app.WATCH";
    static final String ACCEPT = "net.netplay.app.ACCEPT";
    static final String DECLINE = "net.netplay.app.DECLINE";

    @Override public void onReceive(Context c, Intent i) {
        Hub h = Hub.get(c);
        String a = i.getAction() == null ? "" : i.getAction();
        String call = i.getStringExtra("call");
        switch (a) {
            case LEAVE: h.leave(); break;
            case STOP: h.leave(); h.doneSession(); h.stopPresence(); break;
            case WATCH: h.watchToggle(); break;
            case ACCEPT: if (call != null) h.answer(call, true); break;
            case DECLINE: if (call != null) h.answer(call, false); break;
            default: break;
        }
    }
}
