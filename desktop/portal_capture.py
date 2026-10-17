"""Screen/window capture on Wayland through the desktop portal (xdg-desktop-portal ScreenCast).

The desktop itself shows its own "share a screen or window" picker, which is exactly what we want:
the host chooses the emulator window and nothing else is ever shared. We get back a PipeWire
file descriptor and node id for GStreamer's pipewiresrc.

NOTE: written from the portal's documented D-Bus API, not run against a real desktop in the
sandbox it was written in. If it misbehaves, run with --capture x11 (needs an X11/XWayland window)
and send the error text.
"""
import os

from gi.repository import Gio, GLib


TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".netplay-matchmaker", "share-choice.token")


def _load_token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()[:200]
    except OSError:
        return ""


def _save_token(tok):
    """Only the desktop's own token for 'share the same window as last time'; nothing about the window itself."""
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            f.write(str(tok))
    except OSError:
        pass


def _sender_token(bus):
    return bus.get_unique_name().lstrip(":").replace(".", "_")


def request_screencast(timeout=120):
    """Returns (pipewire_fd, node_id). Raises RuntimeError with a plain message on any failure."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    portal = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, "org.freedesktop.portal.Desktop",
                                    "/org/freedesktop/portal/desktop", "org.freedesktop.portal.ScreenCast", None)
    sender, counter = _sender_token(bus), [0]
    loop = GLib.MainLoop()

    def call(method, args_fn):
        """Make a portal call and wait for its Response signal. Returns the results dict."""
        counter[0] += 1
        token = f"netplay{os.getpid()}_{counter[0]}"
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        box = {}

        def on_response(_c, _s, _p, _i, _sig, params):
            box["code"], box["results"] = params.unpack()
            loop.quit()
        sub = bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request", "Response",
                                   path, None, Gio.DBusSignalFlags.NONE, on_response)
        timer = None
        try:
            portal.call_sync(method, args_fn(token), Gio.DBusCallFlags.NONE, -1, None)
            timer = GLib.timeout_add_seconds(timeout, lambda: (loop.quit(), False)[1])
            loop.run()
        finally:
            bus.signal_unsubscribe(sub)
            if timer:
                GLib.source_remove(timer)
        if "code" not in box:
            raise RuntimeError("Timed out waiting for the screen-share picker.")
        if box["code"] != 0:
            raise RuntimeError("Screen sharing was cancelled.")
        return box["results"]

    def opts(token, **extra):
        d = {"handle_token": GLib.Variant("s", token)}
        d.update(extra)
        return d

    res = call("CreateSession", lambda t: GLib.Variant("(a{sv})", (opts(t, session_handle_token=GLib.Variant("s", t + "s")),)))
    session = res["session_handle"]
    # types: 1 = monitor, 2 = window. cursor_mode 1 = hidden. persist_mode 2 = remember the choice, so the
    # "share your screen" box only asks the first time (the desktop hands back a token for next time).
    extra = dict(types=GLib.Variant("u", 3), multiple=GLib.Variant("b", False), cursor_mode=GLib.Variant("u", 1),
                 persist_mode=GLib.Variant("u", 2))
    saved = _load_token()
    if saved:
        extra["restore_token"] = GLib.Variant("s", saved)
    try:
        call("SelectSources", lambda t: GLib.Variant("(oa{sv})", (session, opts(t, **extra))))
    except GLib.Error:
        # older portals don't know persist_mode / restore_token: ask the plain way
        extra.pop("persist_mode", None)
        extra.pop("restore_token", None)
        call("SelectSources", lambda t: GLib.Variant("(oa{sv})", (session, opts(t, **extra))))
    res = call("Start", lambda t: GLib.Variant("(osa{sv})", (session, "", opts(t))))
    if res.get("restore_token"):
        _save_token(res["restore_token"])
    streams = res.get("streams") or []
    if not streams:
        raise RuntimeError("No screen or window was shared.")
    node_id = streams[0][0]
    fd_list = Gio.UnixFDList.new()
    _out, fds = portal.call_with_unix_fd_list_sync(
        "OpenPipeWireRemote", GLib.Variant("(oa{sv})", (session, {})), Gio.DBusCallFlags.NONE, -1, fd_list, None)
    fd = fds.get(_out.unpack()[0])
    return fd, node_id
