package net.netplay.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Buttons on the notifications. Not exported: only this app's own notifications can send these. */
public class ActionReceiver extends BroadcastReceiver {
    static final String LEAVE = "net.netplay.app.LEAVE";
    static final String STOP = "net.netplay.app.STOP";
    static final String ACCEPT = "net.netplay.app.ACCEPT";
    static final String DECLINE = "net.netplay.app.DECLINE";
    static final String REQ_YES = "net.netplay.app.REQ_YES";
    static final String REQ_NO = "net.netplay.app.REQ_NO";

    @Override public void onReceive(Context c, Intent i) {
        Hub h = Hub.get(c);
        String a = i.getAction() == null ? "" : i.getAction();
        String call = i.getStringExtra("call");
        switch (a) {
            case LEAVE: h.leave(); break;
            case STOP: h.leave(); h.doneSession(); h.stopPresence(); break;
            case ACCEPT: if (call != null) h.answer(call, true); break;
            case DECLINE: if (call != null) h.answer(call, false); break;
            case REQ_YES: if (i.getStringExtra("friend") != null) h.answerRequest(i.getStringExtra("friend"), true); break;
            case REQ_NO: if (i.getStringExtra("friend") != null) h.answerRequest(i.getStringExtra("friend"), false); break;
            default: break;
        }
    }
}
