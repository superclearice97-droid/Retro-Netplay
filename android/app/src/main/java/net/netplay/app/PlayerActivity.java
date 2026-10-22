package net.netplay.app;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import net.netplay.core.Json;
import net.netplay.core.Pad;
import net.netplay.core.PlayerText;
import net.netplay.core.StreamSignal;

import org.webrtc.DataChannel;
import org.webrtc.DefaultVideoDecoderFactory;
import org.webrtc.EglBase;
import org.webrtc.IceCandidate;
import org.webrtc.MediaConstraints;
import org.webrtc.MediaStream;
import org.webrtc.MediaStreamTrack;
import org.webrtc.PeerConnection;
import org.webrtc.PeerConnectionFactory;
import org.webrtc.RTCStats;
import org.webrtc.RendererCommon;
import org.webrtc.RtpTransceiver;
import org.webrtc.SdpObserver;
import org.webrtc.SessionDescription;
import org.webrtc.SurfaceViewRenderer;
import org.webrtc.VideoTrack;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The phone's own stream player (no browser, no web page): connects to the matchmaker's stream connection with this
 * side's session key, sets up WebRTC with the friend's computer, shows the video (decoded by the phone's video chip
 * through Android's MediaCodec) and sends this device's controller, or the on-screen buttons, back to the game.
 */
public class PlayerActivity extends Activity {
    private static final String TAG = "NetplayPlayer";

    /** (automatic tests) what the last session saw: frames, inputs sent, the decoder, how it ended. */
    static volatile Map<String, Object> lastResult;

    /** The app's own link to a player session (never registered with Android: only this app opens it). */
    static String link(String host, int port, String key, String game, boolean useRelay) {
        return new Uri.Builder().scheme("player").authority("open").appendQueryParameter("h", host)
                .appendQueryParameter("p", String.valueOf(port)).appendQueryParameter("k", key)
                .appendQueryParameter("g", game == null ? "" : game).appendQueryParameter("r", useRelay ? "1" : "0")
                .build().toString();
    }

    static Intent intent(Context c, String link) {
        return new Intent(c, PlayerActivity.class).setData(Uri.parse(link))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
    }

    static void open(Context c, String link) { c.startActivity(intent(c, link)); }

    private final Handler main = new Handler(Looper.getMainLooper());
    private SurfaceViewRenderer video;
    private TextView message, stats, slotChip;
    private View closeButton;
    private TouchPad touch;
    private final Pad pad = new Pad();
    private int padButtons, touchButtons, hatBits;

    private String game = "";
    private StreamSignal sig;
    private EglBase egl;
    private PeerConnectionFactory factory;
    private PeerConnection pc;
    private DataChannel padCh, ctlCh;
    private final List<IceCandidate> early = new ArrayList<>();
    private boolean useRelay, videoReady, remoteSet, relay, heard, playing, ended, statsOn, testInput;
    private long startedAt, videoAt, backAt, testSeconds;
    private int slot;
    private Double rttMs;
    private Map<String, Object> hostStats = new LinkedHashMap<>();
    private long framesDecoded;
    private String decoder = "";
    private double lastStatsAt;
    private long lastBytes, lastFrames;
    private double lastDecodeTime, lastJbDelay;
    private long lastJbCount;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        Uri u = getIntent().getData();
        String host = u == null ? null : u.getQueryParameter("h"), key = u == null ? null : u.getQueryParameter("k");
        int port;
        try { port = Integer.parseInt(u == null ? "" : u.getQueryParameter("p")); } catch (RuntimeException e) { port = 0; }
        if (u == null || !"player".equals(u.getScheme()) || host == null || key == null || port <= 0) {
            finish();
            return;
        }
        game = u.getQueryParameter("g") == null ? "" : u.getQueryParameter("g");
        useRelay = !"0".equals(u.getQueryParameter("r"));
        testSeconds = getIntent().getLongExtra("test_seconds", 0);
        testInput = getIntent().getBooleanExtra("test_input", false);
        lastResult = null;
        buildScreen();
        startedAt = SystemClock.uptimeMillis();
        say(game.isEmpty() ? "Connecting to the matchmaker..." : "Connecting to the matchmaker for " + game + "...");
        StreamSignal s = new StreamSignal(host, port, key, new StreamSignal.Listener() {
            @Override public void onMessage(Map<String, Object> msg) { main.post(() -> onSignal(msg)); }
            @Override public void onEnd(String why) {
                main.post(() -> { if (!ended) end(PlayerText.end(why, relay), !why.equals("session closed") && !why.equals("session ended")); });
            }
        });
        sig = s;
        new Thread(() -> {
            try {
                Map<String, Object> cfg = s.connect();
                main.post(() -> { if (!ended) beginMedia(cfg); });
            } catch (StreamSignal.Failure f) {
                main.post(() -> end(f.getMessage(), true));
            }
        }, "player-connect").start();
        main.post(tick);
        main.post(padLoop);
    }

    // ---------------------------------------------------------------- the screen
    private void buildScreen() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF000000);
        video = new SurfaceViewRenderer(this);
        root.addView(video, new FrameLayout.LayoutParams(-1, -1, Gravity.CENTER));
        touch = new TouchPad(this, bits -> { touchButtons = bits; pad.buttons = padButtons | touchButtons; });
        touch.setVisibility(hasController() ? View.GONE : View.VISIBLE);
        root.addView(touch, new FrameLayout.LayoutParams(-1, -1));

        stats = Ink.text(this, "", 11, false, 0xFFFFFFFF);
        stats.setBackgroundColor(0x99000000);
        stats.setPadding(dp(8), dp(6), dp(8), dp(6));
        stats.setVisibility(View.GONE);
        FrameLayout.LayoutParams sl = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.START);
        sl.setMargins(dp(10), dp(52), 0, 0);
        root.addView(stats, sl);

        LinearLayout chips = new LinearLayout(this);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        slotChip = chip("", null);
        slotChip.setVisibility(View.GONE);
        chips.addView(slotChip);
        chips.addView(chip("Stats", v -> { statsOn = !statsOn; stats.setVisibility(statsOn ? View.VISIBLE : View.GONE); }));
        chips.addView(chip("Buttons", v -> {
            touch.release();
            touch.setVisibility(touch.getVisibility() == View.VISIBLE ? View.GONE : View.VISIBLE);
        }));
        chips.addView(chip("Leave", v -> leave()));
        FrameLayout.LayoutParams cl = new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        cl.setMargins(0, dp(8), 0, 0);
        root.addView(chips, cl);

        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setGravity(Gravity.CENTER_HORIZONTAL);
        message = Ink.text(this, "", 15, false, Ink.INK);
        message.setGravity(Gravity.CENTER);
        message.setBackground(Ink.box(Ink.PAPER, Ink.INK, 1, this));
        message.setPadding(dp(16), dp(12), dp(16), dp(12));
        card.addView(message);
        InkButton close = new InkButton(this, Icons.CLOSE, "Close", true);
        close.onPress(this::finish);
        close.setVisibility(View.GONE);
        closeButton = close;
        LinearLayout.LayoutParams bl = new LinearLayout.LayoutParams(-2, -2);
        bl.setMargins(0, dp(10), 0, 0);
        card.addView(close, bl);
        FrameLayout.LayoutParams ml = new FrameLayout.LayoutParams(-2, -2, Gravity.CENTER);
        ml.setMargins(dp(24), dp(24), dp(24), dp(24));
        root.addView(card, ml);
        setContentView(root);
    }

    private TextView chip(String text, View.OnClickListener l) {
        TextView t = Ink.text(this, text, 12, true, 0xFFFFFFFF);
        t.setBackgroundColor(0x99000000);
        t.setPadding(dp(12), dp(6), dp(12), dp(6));
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-2, -2);
        p.setMargins(dp(4), 0, dp(4), 0);
        t.setLayoutParams(p);
        if (l != null) t.setOnClickListener(l);
        return t;
    }

    private int dp(float v) { return Ink.dp(this, v); }

    private void say(String text) {
        if (playing || ended) return;
        message.setText(text);
        message.setVisibility(View.VISIBLE);
    }

    @Override public void onWindowFocusChanged(boolean has) {
        super.onWindowFocusChanged(has);
        if (has) {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    | View.SYSTEM_UI_FLAG_FULLSCREEN | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        }
    }

    // ---------------------------------------------------------------- WebRTC
    private static boolean initialized;

    private void beginMedia(Map<String, Object> cfg) {
        relay = useRelay && Boolean.TRUE.equals(cfg.get("relay"));
        try {
            if (!initialized) {
                PeerConnectionFactory.initialize(PeerConnectionFactory.InitializationOptions.builder(getApplicationContext())
                        .createInitializationOptions());
                initialized = true;
            }
            egl = EglBase.create();
            videoReady = true;
            video.init(egl.getEglBaseContext(), new RendererCommon.RendererEvents() {
                @Override public void onFirstFrameRendered() { main.post(PlayerActivity.this::firstFrame); }
                @Override public void onFrameResolutionChanged(int w, int h, int rotation) { }
            });
            video.setScalingType(RendererCommon.ScalingType.SCALE_ASPECT_FIT);
            factory = PeerConnectionFactory.builder()
                    .setVideoDecoderFactory(new DefaultVideoDecoderFactory(egl.getEglBaseContext()))
                    .createPeerConnectionFactory();
            PeerConnection.RTCConfiguration rtc = new PeerConnection.RTCConfiguration(iceServers(cfg.get("iceServers"), useRelay));
            rtc.sdpSemantics = PeerConnection.SdpSemantics.UNIFIED_PLAN;
            rtc.bundlePolicy = PeerConnection.BundlePolicy.MAXBUNDLE;
            rtc.rtcpMuxPolicy = PeerConnection.RtcpMuxPolicy.REQUIRE;
            if (getIntent().getBooleanExtra("test_relay_only", false)) rtc.iceTransportsType = PeerConnection.IceTransportsType.RELAY;
            pc = factory.createPeerConnection(rtc, observer);
            if (pc == null) throw new IllegalStateException("WebRTC didn't start");
        } catch (RuntimeException | UnsatisfiedLinkError e) {
            Log.e(TAG, "player start", e);
            end("The player couldn't start: " + e.getMessage(), true, true);
            return;
        }
        sig.start();
        sig.send(msg("type", "hello"));
        say("Waiting for your friend's computer...");
    }

    /** The matchmaker's ICE servers ({"urls", "username", "credential"}: STUN, and the relay's short-lived password).
     *  useRelay false: the player chose not to send the stream through a relay, so turn: servers are left out. */
    static List<PeerConnection.IceServer> iceServers(Object list, boolean useRelay) {
        List<PeerConnection.IceServer> out = new ArrayList<>();
        if (!(list instanceof List)) return out;
        for (Object o : (List<?>) list) {
            if (!(o instanceof Map)) continue;
            Map<?, ?> m = (Map<?, ?>) o;
            List<String> urls = new ArrayList<>();
            Object u = m.get("urls");
            if (u instanceof String) urls.add((String) u);
            else if (u instanceof List) for (Object x : (List<?>) u) if (x instanceof String) urls.add((String) x);
            if (!useRelay) urls.removeIf(x -> x.startsWith("turn:") || x.startsWith("turns:"));
            if (urls.isEmpty()) continue;
            PeerConnection.IceServer.Builder b = PeerConnection.IceServer.builder(urls);
            if (m.get("username") instanceof String) b.setUsername((String) m.get("username"));
            if (m.get("credential") instanceof String) b.setPassword((String) m.get("credential"));
            out.add(b.createIceServer());
        }
        return out;
    }

    private final PeerConnection.Observer observer = new PeerConnection.Observer() {
        @Override public void onSignalingChange(PeerConnection.SignalingState s) { }
        @Override public void onIceConnectionChange(PeerConnection.IceConnectionState s) {
            main.post(() -> status("ice " + s.name().toLowerCase(Locale.ROOT)));
        }
        @Override public void onConnectionChange(PeerConnection.PeerConnectionState s) {
            main.post(() -> {
                String st = s.name().toLowerCase(Locale.ROOT);
                status("connection " + st);
                if (s == PeerConnection.PeerConnectionState.FAILED && !ended) end(PlayerText.end("ice failed", relay), true, true);
                else if (s == PeerConnection.PeerConnectionState.CONNECTED) say("Connected. The picture is on its way...");
                else if (s == PeerConnection.PeerConnectionState.DISCONNECTED) { padButtons = 0; pad.buttons = touchButtons; }
            });
        }
        @Override public void onIceConnectionReceivingChange(boolean b) { }
        @Override public void onIceGatheringChange(PeerConnection.IceGatheringState s) { }
        @Override public void onIceCandidate(IceCandidate c) {
            Map<String, Object> m = msg("type", "ice");
            m.put("candidate", c.sdp);
            m.put("sdpMLineIndex", (long) c.sdpMLineIndex);
            if (sig != null) sig.send(m);
        }
        @Override public void onIceCandidatesRemoved(IceCandidate[] c) { }
        @Override public void onAddStream(MediaStream s) { }
        @Override public void onRemoveStream(MediaStream s) { }
        @Override public void onDataChannel(DataChannel ch) { main.post(() -> channel(ch)); }
        @Override public void onRenegotiationNeeded() { }
        @Override public void onTrack(RtpTransceiver t) {
            MediaStreamTrack track = t.getReceiver().track();
            if (track instanceof VideoTrack) main.post(() -> { if (!ended) ((VideoTrack) track).addSink(video); });
        }
    };

    private void status(String text) {
        Log.i(TAG, text);
        if (sig != null) sig.send(msg("type", "status", "text", text));
    }

    private void onSignal(Map<String, Object> m) {
        if (ended || pc == null) return;
        Object t = m.get("type");
        if ("offer".equals(t) && m.get("sdp") instanceof String) {
            heard = true;
            say("Connecting the video...");
            remoteSet = false;
            pc.setRemoteDescription(new Sdp() {
                @Override public void onSetSuccess() { main.post(PlayerActivity.this::answer); }
                @Override public void onSetFailure(String e) { main.post(() -> end("The player couldn't use the stream's setup: " + e, true, true)); }
            }, new SessionDescription(SessionDescription.Type.OFFER, (String) m.get("sdp")));
        } else if ("ice".equals(t) && m.get("candidate") instanceof String && !((String) m.get("candidate")).isEmpty()) {
            int idx = m.get("sdpMLineIndex") instanceof Number ? ((Number) m.get("sdpMLineIndex")).intValue() : 0;
            IceCandidate c = new IceCandidate("", idx, (String) m.get("candidate"));
            if (remoteSet) pc.addIceCandidate(c); else early.add(c);
        } else if ("status".equals(t) && m.get("text") instanceof String) {
            heard = true;
            String s = (String) m.get("text");
            say(s.length() > 300 ? s.substring(0, 300) : s);
        } else if ("bye".equals(t)) {
            end(PlayerText.bye(m.get("why")), m.get("why") != null && !String.valueOf(m.get("why")).isEmpty());
        }
    }

    private void answer() {
        if (ended || pc == null) return;
        remoteSet = true;
        for (IceCandidate c : early) pc.addIceCandidate(c);
        early.clear();
        pc.createAnswer(new Sdp() {
            @Override public void onCreateSuccess(SessionDescription d) {
                main.post(() -> {
                    if (ended || pc == null) return;
                    pc.setLocalDescription(new Sdp(), d);
                    sig.send(msg("type", "answer", "sdp", d.description));
                });
            }
            @Override public void onCreateFailure(String e) { main.post(() -> end("The player couldn't answer the stream: " + e, true, true)); }
        }, new MediaConstraints());
    }

    private static class Sdp implements SdpObserver {
        @Override public void onCreateSuccess(SessionDescription d) { }
        @Override public void onSetSuccess() { }
        @Override public void onCreateFailure(String e) { Log.w(TAG, "sdp: " + e); }
        @Override public void onSetFailure(String e) { Log.w(TAG, "sdp: " + e); }
    }

    private void channel(DataChannel ch) {
        String label = ch.label();
        if ("pad".equals(label)) padCh = ch;
        else if ("ctl".equals(label)) ctlCh = ch;
        else return;
        boolean[] opened = {false};
        Runnable open = () -> {
            if (opened[0] || ch.state() != DataChannel.State.OPEN) return;      // (a channel can say "open" twice)
            opened[0] = true;
            if (ch == ctlCh) ctl(msg("t", "hi", "ua", "Retro-Netplay player (Android)"));   // the host answers with our slot
        };
        ch.registerObserver(new DataChannel.Observer() {
            @Override public void onBufferedAmountChange(long l) { }
            @Override public void onStateChange() { main.post(open); }
            @Override public void onMessage(DataChannel.Buffer buf) {
                if (ch != ctlCh || buf.binary) return;
                byte[] bytes = new byte[buf.data.remaining()];
                buf.data.get(bytes);
                String text = new String(bytes, StandardCharsets.UTF_8);
                main.post(() -> onCtl(text));
            }
        });
        open.run();          // it is often open already when it arrives, and then says nothing more
    }

    private void ctl(Map<String, Object> m) {
        DataChannel c = ctlCh;
        if (c == null || c.state() != DataChannel.State.OPEN) return;
        c.send(new DataChannel.Buffer(ByteBuffer.wrap(Json.stringify(m).getBytes(StandardCharsets.UTF_8)), false));
    }

    private void onCtl(String text) {
        Map<String, Object> m;
        try { m = Json.parseObject(text); } catch (RuntimeException e) { return; }
        Object t = m.get("t");
        if ("pong".equals(t) && m.get("ts") instanceof Number) {
            rttMs = SystemClock.uptimeMillis() - ((Number) m.get("ts")).doubleValue();
        } else if ("slot".equals(t) && m.get("n") instanceof Number) {
            slot = ((Number) m.get("n")).intValue();
            slotChip.setText("Player " + slot);
            slotChip.setVisibility(View.VISIBLE);
            Log.i(TAG, "you are Player " + slot);
        } else if ("host".equals(t) && m.get("s") instanceof Map) {
            @SuppressWarnings("unchecked") Map<String, Object> s = (Map<String, Object>) m.get("s");
            hostStats = s;
        }
    }

    private void firstFrame() {
        if (ended || playing) return;
        playing = true;
        videoAt = SystemClock.uptimeMillis();
        message.setVisibility(View.GONE);
        Log.i(TAG, "video is playing");
    }

    // ---------------------------------------------------------------- once a second: statistics, timeouts, test time
    private final Runnable tick = new Runnable() {
        @Override public void run() {
            if (ended) return;
            long now = SystemClock.uptimeMillis();
            if (pc != null) {
                ctl(msg("t", "ping", "ts", (double) now));
                pc.getStats(report -> main.post(() -> readStats(report.getStatsMap().values())));
            }
            if (!heard && !playing && now - startedAt > 20000) say(PlayerText.end("no host", relay));
            if (heard && !playing && now - startedAt > 45000) say(PlayerText.end("no video", relay));
            if (testSeconds > 0 && videoAt == 0 && now - startedAt > 30000) {
                end(PlayerText.end(heard ? "no video" : "no host", relay), true, true);
                return;
            }
            if (testSeconds > 0 && videoAt > 0 && now - videoAt > testSeconds * 1000) {
                Log.i(TAG, "test time is up");
                leave();
                return;
            }
            main.postDelayed(this, 1000);
        }
    };

    private void readStats(Iterable<RTCStats> all) {
        if (ended) return;
        for (RTCStats st : all) {
            Map<String, Object> m = st.getMembers();
            if (!"inbound-rtp".equals(st.getType()) || !"video".equals(m.get("kind"))) continue;
            double t = st.getTimestampUs() / 1e6;
            long bytes = num(m.get("bytesReceived")), frames = num(m.get("framesDecoded")), jbCount = num(m.get("jitterBufferEmittedCount"));
            double decodeTime = dbl(m.get("totalDecodeTime")), jbDelay = dbl(m.get("jitterBufferDelay"));
            framesDecoded = frames;
            if (m.get("decoderImplementation") instanceof String) decoder = (String) m.get("decoderImplementation");
            Map<String, Object> s = new LinkedHashMap<>();
            s.put("rttMs", rttMs == null ? null : Math.round(rttMs * 10) / 10.0);
            if (lastStatsAt > 0 && t > lastStatsAt) {
                double dt = t - lastStatsAt;
                long df = frames - lastFrames;
                s.put("fps", Math.round(df / dt));
                s.put("kbps", Math.round((bytes - lastBytes) * 8 / dt / 1000));
                s.put("decodeMs", df > 0 ? Math.round((decodeTime - lastDecodeTime) / df * 10000) / 10.0 : null);
                long dj = jbCount - lastJbCount;
                s.put("jitterBufMs", dj > 0 ? Math.round((jbDelay - lastJbDelay) / dj * 1000) : null);
            }
            s.put("lost", num(m.get("packetsLost")));
            s.put("dropped", num(m.get("framesDropped")));
            s.put("w", num(m.get("frameWidth")));
            s.put("h", num(m.get("frameHeight")));
            if (s.get("rttMs") instanceof Double && s.get("decodeMs") instanceof Double && s.get("jitterBufMs") instanceof Long) {
                s.put("addedMs", Math.round(((Double) s.get("rttMs") / 2 + (Long) s.get("jitterBufMs") + (Double) s.get("decodeMs")) * 10) / 10.0);
            }
            lastStatsAt = t; lastBytes = bytes; lastFrames = frames; lastDecodeTime = decodeTime; lastJbDelay = jbDelay; lastJbCount = jbCount;
            if (s.containsKey("fps")) ctl(msg("t", "stats", "s", s));
            if (statsOn) stats.setText(overlay(s));
        }
    }

    private String overlay(Map<String, Object> s) {
        StringBuilder b = new StringBuilder();
        b.append(String.format(Locale.ROOT, "%sx%s  %s fps  %s kbps\n", s.get("w"), s.get("h"), s.get("fps"), s.get("kbps")));
        b.append(String.format(Locale.ROOT, "network %s ms  buffer %s ms  decode %s ms\n", s.get("rttMs"), s.get("jitterBufMs"), s.get("decodeMs")));
        b.append(String.format(Locale.ROOT, "lost %s  dropped %s  decoder %s", s.get("lost"), s.get("dropped"), decoder.isEmpty() ? "?" : decoder));
        if (hostStats.get("totalMs") != null) {
            b.append(String.format(Locale.ROOT, "\nfriend's computer: encode %s ms, total %s ms", hostStats.get("encodeMs"), hostStats.get("totalMs")));
        }
        return b.toString();
    }

    private static long num(Object o) { return o instanceof Number ? ((Number) o).longValue() : 0; }
    private static double dbl(Object o) { return o instanceof Number ? ((Number) o).doubleValue() : 0; }

    // ---------------------------------------------------------------- controller
    /** Sends the controller on every change, and again every 50 ms while nothing changes (the host's watchdog). */
    private final Runnable padLoop = new Runnable() {
        private long testAt;
        @Override public void run() {
            if (ended) return;
            long now = SystemClock.uptimeMillis();
            DataChannel c = padCh;
            if (c != null && c.state() == DataChannel.State.OPEN) {
                if (testInput && now - testAt > 400) {        // (automatic tests: press and release A)
                    testAt = now;
                    padButtons ^= 1;
                    pad.buttons = padButtons | touchButtons;
                }
                byte[] p = pad.next(now);
                if (p != null) c.send(new DataChannel.Buffer(ByteBuffer.wrap(p), true));
            }
            main.postDelayed(this, 8);
        }
    };

    private boolean hasController() {
        for (int id : InputDevice.getDeviceIds()) {
            InputDevice d = InputDevice.getDevice(id);
            if (d != null && !d.isVirtual() && (d.getSources() & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD) return true;
        }
        return false;
    }

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
        boolean fromPad = (e.getSource() & (InputDevice.SOURCE_GAMEPAD | InputDevice.SOURCE_JOYSTICK | InputDevice.SOURCE_DPAD)) != 0;
        if (n < 0 || !fromPad) return super.dispatchKeyEvent(e);      // e.g. Back
        if (e.getAction() == KeyEvent.ACTION_DOWN) padButtons |= 1 << n;
        else if (e.getAction() == KeyEvent.ACTION_UP) padButtons &= ~(1 << n);
        pad.buttons = padButtons | touchButtons;
        if (touch.getVisibility() == View.VISIBLE && e.getAction() == KeyEvent.ACTION_DOWN) {
            touch.release();
            touch.setVisibility(View.GONE);                              // a controller is in use: clear the screen
        }
        return true;
    }

    @Override public boolean dispatchGenericMotionEvent(MotionEvent e) {
        if ((e.getSource() & InputDevice.SOURCE_JOYSTICK) == 0 || e.getAction() != MotionEvent.ACTION_MOVE) {
            return super.dispatchGenericMotionEvent(e);
        }
        pad.ax[0] = e.getAxisValue(MotionEvent.AXIS_X);
        pad.ax[1] = e.getAxisValue(MotionEvent.AXIS_Y);
        pad.ax[2] = e.getAxisValue(MotionEvent.AXIS_Z);
        pad.ax[3] = e.getAxisValue(MotionEvent.AXIS_RZ);
        pad.lt = trigger(Math.max(e.getAxisValue(MotionEvent.AXIS_LTRIGGER), e.getAxisValue(MotionEvent.AXIS_BRAKE)));
        pad.rt = trigger(Math.max(e.getAxisValue(MotionEvent.AXIS_RTRIGGER), e.getAxisValue(MotionEvent.AXIS_GAS)));
        float hx = e.getAxisValue(MotionEvent.AXIS_HAT_X), hy = e.getAxisValue(MotionEvent.AXIS_HAT_Y);
        int hat = (hy < -0.5f ? 1 << 12 : 0) | (hy > 0.5f ? 1 << 13 : 0) | (hx < -0.5f ? 1 << 14 : 0) | (hx > 0.5f ? 1 << 15 : 0);
        padButtons = (padButtons & ~hatBits) | hat;
        hatBits = hat;
        pad.buttons = padButtons | touchButtons;
        return true;
    }

    private static int trigger(float v) { return Math.max(0, Math.min(255, Math.round(v * 255))); }

    // ---------------------------------------------------------------- leaving
    @Override public void onBackPressed() {
        if (ended) { finish(); return; }
        long now = SystemClock.uptimeMillis();
        if (now - backAt < 2500) { leave(); return; }
        backAt = now;
        android.widget.Toast.makeText(this, "Press Back again to leave the stream.", android.widget.Toast.LENGTH_SHORT).show();
    }

    /** Leave on purpose: tell the host (no reason, so it shows "your friend left"), then close. */
    private void leave() {
        end("You left the stream.", false, true);
        finish();
    }

    private void end(String text, boolean error) { end(text, error, false); }

    /** tellHost: say goodbye to the friend's computer (not when the ending came from it or the matchmaker). */
    private void end(String text, boolean error, boolean tellHost) {
        if (ended) return;
        ended = true;
        Log.i(TAG, "ended: " + text);
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("frames", framesDecoded);
        r.put("playing", videoAt > 0);
        r.put("inputs", (long) pad.sent);
        r.put("slot", (long) slot);
        r.put("decoder", decoder);
        r.put("relay", relay);
        r.put("ended", text);
        r.put("error", error);
        r.put("host", hostStats);
        lastResult = r;
        Log.i(TAG, "RESULT " + Json.stringify(r));
        message.setText(text);
        message.setVisibility(View.VISIBLE);
        closeButton.setVisibility(View.VISIBLE);
        if (touch != null) touch.setVisibility(View.GONE);
        StreamSignal s = sig;
        if (s != null && tellHost) {
            s.send(msg("type", "bye"));
            new Thread(() -> { s.flush(1500); s.stop(); }, "player-bye").start();
        } else if (s != null) {
            s.stop();
        }
        closeMedia();
    }

    private void closeMedia() {
        PeerConnection p = pc;
        pc = null;
        padCh = null;
        ctlCh = null;
        if (p != null) { p.close(); p.dispose(); }
        if (videoReady) { video.release(); videoReady = false; }
        if (factory != null) { factory.dispose(); factory = null; }
        if (egl != null) { egl.release(); egl = null; }
    }

    @Override protected void onDestroy() {
        main.removeCallbacks(tick);
        main.removeCallbacks(padLoop);
        if (!ended) end("You left the stream.", false, true);
        super.onDestroy();
    }

    private static Map<String, Object> msg(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }
}
