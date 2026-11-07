package net.netplay.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import net.netplay.core.Arcade;
import net.netplay.core.Games;
import net.netplay.core.Json;
import net.netplay.core.Lan;
import net.netplay.core.LocalApi;

/**
 * The phone app: the same arcade interface as the desktop (assets/ui, copied from desktop/ui at build time) in a
 * full-screen web view, served by the phone's own engine (core Arcade + LocalApi) on 127.0.0.1. Stream rooms open in
 * the app's player window; netplay rooms open RetroArch; "Add a folder" opens the phone's folder picker.
 */
public class ArcadeActivity extends Activity implements Setup.Host {
    private static Arcade arcade;          // one engine for the app's lifetime (the lobby connection stays up)
    private static LocalApi api;
    private WebView web;
    private Setup setup;
    private SharedPreferences prefs;

    @SuppressLint("SetJavaScriptEnabled")
    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        prefs = getSharedPreferences("nm", MODE_PRIVATE);
        setup = new Setup(this, prefs, this);
        synchronized (ArcadeActivity.class) {
            if (arcade == null) {
                PhonePlatform pf = new PhonePlatform(getApplicationContext());
                arcade = new Arcade(pf);
                api = new LocalApi(arcade, pf::asset, pf::art);
                try { api.start(); } catch (IOException e) { throw new RuntimeException(e); }
                arcade.start();
            }
        }
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0d1110"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        web.addJavascriptInterface(new Bridge(), "shell");
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                if ("127.0.0.1".equals(u.getHost()) && u.getPort() == api.port) return false;
                if ("https".equals(u.getScheme())) startActivity(new Intent(Intent.ACTION_VIEW, u));      // other sites: the browser
                return true;
            }
        });
        setContentView(web);
        web.loadUrl(api.url());
        setup.background(false);        // pick up new games in the folders
        link(getIntent());
    }

    @Override protected void onNewIntent(Intent i) { super.onNewIntent(i); link(i); }

    /** netplay://lobby?h=..&p=..[&code=..] links: hand them to the page, which knows what to do. */
    private void link(Intent i) {
        Uri u = i == null ? null : i.getData();
        if (u == null || !"netplay".equals(u.getScheme())) return;
        String js = "window.dispatchEvent(new CustomEvent('netplay-link', {detail: " + Json.stringify(u.toString()) + "}))";
        web.postDelayed(() -> web.evaluateJavascript(js, null), 1500);
    }

    @Override protected void onActivityResult(int req, int res, Intent data) {
        super.onActivityResult(req, res, data);
        if (req == Setup.PICK_FOLDER && res == RESULT_OK && data != null) setup.onFolderPicked(data.getData());
    }

    @Override public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    // Setup.Host: the folders or games changed: tell the page (a no-op settings action sends the new state)
    @Override public void setupChanged() { arcade.act(map("action", "settings")); }

    /** What the page may ask of the phone. */
    final class Bridge {
        @JavascriptInterface public String pickFolder() {
            runOnUiThread(() -> setup.step(1, true));      // the phone's own "Your games" sheet, with its folder picker
            return "";
        }
    }

    static Uri uri(String where) {
        return where.startsWith("content:") || where.startsWith("file:") ? Uri.parse(where) : Uri.fromFile(new File(where));
    }

    private static byte[] readAll(InputStream in) throws IOException {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        int n;
        while ((n = in.read(buf)) > 0) o.write(buf, 0, n);
        return o.toByteArray();
    }

    static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    /** What only the phone can do, for the engine (the application's context only, so it outlives any window). */
    static final class PhonePlatform implements Arcade.Platform {
        private final Context c;
        private final SharedPreferences prefs;
        private final Set<String> artPaths = java.util.Collections.synchronizedSet(new HashSet<>());

        PhonePlatform(Context c) { this.c = c; prefs = c.getSharedPreferences("nm", MODE_PRIVATE); }

        byte[] asset(String path) {
            try (InputStream in = c.getAssets().open("ui/" + path)) { return readAll(in); }
            catch (IOException e) { return null; }
        }

        byte[] art(String path) {
            if (!artPaths.contains(path)) return null;          // only the library's own pictures
            try (InputStream in = c.getContentResolver().openInputStream(uri(path))) { return in == null ? null : readAll(in); }
            catch (Exception e) { return null; }
        }

        @SuppressWarnings("unchecked")
        @Override public Map<String, Object> loadSettings() {
            String s = Secret.get(prefs, "arcade");             // (encrypted in the app's private storage)
            try { return s == null || s.isEmpty() ? new LinkedHashMap<>() : Json.parseObject(s); }
            catch (RuntimeException e) { return new LinkedHashMap<>(); }
        }

        @Override public void saveSettings(Map<String, Object> settings) { Secret.put(prefs, "arcade", Json.stringify(settings)); }

        @SuppressWarnings("unchecked")
        @Override public List<Map<String, Object>> library() {
            List<Map<String, Object>> out = new ArrayList<>();
            List<Object> games;
            try { games = (List<Object>) Json.parse(prefs.getString("games", "[]")); } catch (RuntimeException e) { games = new ArrayList<>(); }
            boolean ra = retroArch() != null;
            int i = 0;
            for (Object o : games) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> g = (Map<String, Object>) o;
                String sys = String.valueOf(g.get("system")), name = String.valueOf(g.get("name"));
                Games.Sys s = Games.SYSTEMS.get(sys);
                if (s == null) continue;
                Map<String, Object> art = g.get("art") instanceof Map ? (Map<String, Object>) g.get("art") : new LinkedHashMap<>();
                List<Object> screens = art.get("screens") instanceof List ? (List<Object>) art.get("screens") : new ArrayList<>();
                Map<String, Object> e = map("i", i++, "name", art.get("name") instanceof String && !((String) art.get("name")).isEmpty() ? art.get("name") : name,
                        "system", sys, "key", Games.key(sys, name), "rom", g.get("rom"),
                        "problem", !s.hasNative() ? "" : !ra ? "Install RetroArch to play this here." : "",
                        "screens", screens);
                for (String k : new String[]{"box", "title", "logo", "fanart", "video", "year", "desc", "developer", "publisher", "genre", "players", "rating"})
                    e.put(k, art.get(k) == null ? "" : String.valueOf(art.get(k)));
                for (Object p : screens) artPaths.add(String.valueOf(p));
                for (String k : new String[]{"box", "title", "logo", "fanart"}) if (!String.valueOf(e.get(k)).isEmpty()) artPaths.add(String.valueOf(e.get(k)));
                out.add(e);
            }
            return out;
        }

        @Override public void openPlayer(String host, int port, String key, String hostName, boolean useRelay) {
            PlayerActivity.open(c, PlayerActivity.link(host, port, key, hostName, useRelay));
        }

        @Override public String openNetplay(Map<String, Object> game, Map<String, Object> nat) {
            Games.Sys s = Games.SYSTEMS.get(String.valueOf(game.get("system")));
            if ("dolphin".equals(nat.get("emu")))
                return "This room uses Dolphin netplay, which isn't set up on phones yet. Join a stream room, or play it on a computer.";
            String pkg = retroArch();
            if (s == null || !s.hasNative() || pkg == null) return "Install RetroArch to play this here.";
            String ip = String.valueOf(nat.get("ip"));
            try {
                Intent i = new Intent(Intent.ACTION_MAIN);
                i.setComponent(new ComponentName(pkg, "com.retroarch.browser.retroactivity.RetroActivityFuture"));
                i.putExtra("ROM", String.valueOf(game.get("rom")));
                i.putExtra("LIBRETRO", "/data/data/" + pkg + "/cores/" + s.core + "_libretro_android.so");
                i.putExtra("CONFIGFILE", "/storage/emulated/0/Android/data/" + pkg + "/files/retroarch.cfg");
                i.putExtra("QUITFOCUS", "");
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
                c.startActivity(i);
            } catch (RuntimeException e) {
                return "Couldn't start RetroArch: " + e.getMessage();
            }
            ((ClipboardManager) c.getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("host", ip));
            return "RetroArch is opening. Once the game runs, open RetroArch's menu > Netplay > Connect to Netplay Host, and enter "
                    + ip + " (copied).";
        }

        @Override public void stopGames() { /* the player window closes by itself when the room ends */ }

        @Override public List<String> findLobbies() {
            List<String> out = new ArrayList<>();
            try {
                Lan.Found best = Lan.pick(Lan.findAll(broadcastTargets(), myWifiIp(), Lan.DEFAULT_PORT));
                if (best != null) out.add(best.host + ":" + best.port);
            } catch (RuntimeException ignored) { }
            return out;
        }

        /** Where to send "is there a lobby here?" on this Wi-Fi (as Hub does). */
        private List<String> broadcastTargets() {
            List<String> t = new ArrayList<>();
            t.add("255.255.255.255");
            try {
                android.net.wifi.WifiManager w = (android.net.wifi.WifiManager) c.getSystemService(Context.WIFI_SERVICE);
                @SuppressWarnings("deprecation") android.net.DhcpInfo d = w.getDhcpInfo();
                if (d != null && d.ipAddress != 0) {
                    int mask = d.netmask != 0 ? d.netmask : 0x00FFFFFF;
                    int b = (d.ipAddress & mask) | ~mask;
                    t.add((b & 0xFF) + "." + ((b >> 8) & 0xFF) + "." + ((b >> 16) & 0xFF) + "." + ((b >> 24) & 0xFF));
                }
            } catch (RuntimeException ignored) { }
            return t;
        }

        private String myWifiIp() {
            try {
                android.net.wifi.WifiManager w = (android.net.wifi.WifiManager) c.getSystemService(Context.WIFI_SERVICE);
                @SuppressWarnings("deprecation") int ip = w.getConnectionInfo().getIpAddress();
                return ip == 0 ? "" : (ip & 0xFF) + "." + ((ip >> 8) & 0xFF) + "." + ((ip >> 16) & 0xFF) + "." + ((ip >> 24) & 0xFF);
            } catch (RuntimeException e) { return ""; }
        }

        private String retroArch() {
            for (String pkg : new String[]{"com.retroarch.aarch64", "com.retroarch", "com.retroarch.ra32"}) {
                try { c.getPackageManager().getPackageInfo(pkg, 0); return pkg; } catch (PackageManager.NameNotFoundException ignored) { }
            }
            return null;
        }
    }
}
