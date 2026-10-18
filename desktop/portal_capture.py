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


def _prop(portal, name):
    v = portal.get_cached_property(name)
    return v.unpack() if v is not None else None


def request_screencast(timeout=120, on_slow=None, slow_after=20, progress=None):
    """Returns (pipewire_fd, node_id). Raises RuntimeError with a plain message on any failure.
    on_slow(): called once if the 'share your screen' box hasn't been answered after slow_after seconds."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    portal = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, "org.freedesktop.portal.Desktop",
                                    "/org/freedesktop/portal/desktop", "org.freedesktop.portal.ScreenCast", None)
    say = progress or (lambda _t: None)
    version, kinds, cursors = _prop(portal, "version"), _prop(portal, "AvailableSourceTypes"), _prop(portal, "AvailableCursorModes")
    say(f"desktop: {os.environ.get('XDG_CURRENT_DESKTOP', '?')}, screen sharing version {version}, "
        f"can share: {kinds} (1 screen, 2 window), cursor modes: {cursors}")
    if version is None:
        raise RuntimeError("This desktop has no screen-sharing service. Install the one for your desktop "
                           "(KDE: xdg-desktop-portal-kde, GNOME: xdg-desktop-portal-gnome, Hyprland: "
                           "xdg-desktop-portal-hyprland), then log out and back in.")
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
        # sender None: match by the request path only. Naming the portal here makes newer GLib (2.80.1+) drop the
        # answer until it has looked up who owns that name, which can lose it, and then the picker never shows.
        sub = bus.signal_subscribe(None, "org.freedesktop.portal.Request", "Response",
                                   path, None, Gio.DBusSignalFlags.NONE, on_response)
        timer = None
        try:
            try:
                portal.call_sync(method, args_fn(token), Gio.DBusCallFlags.NONE, -1, None)
            except GLib.Error as e:
                if any(k in str(e) for k in ("ServiceUnknown", "UnknownMethod", "UnknownInterface", "UnknownObject")):
                    raise RuntimeError("This desktop has no screen-sharing service. Install the one for your desktop "
                                       "(KDE: xdg-desktop-portal-kde, GNOME: xdg-desktop-portal-gnome, Hyprland: "
                                       "xdg-desktop-portal-hyprland), then log out and back in.") from None
                raise RuntimeError(f"The desktop's screen sharing said no: {e.message}") from None
            timer = GLib.timeout_add_seconds(timeout, lambda: (loop.quit(), False)[1])
            loop.run()
        finally:
            bus.signal_unsubscribe(sub)
            if timer:
                GLib.source_remove(timer)
        if "code" not in box:
            raise RuntimeError("Nobody answered the 'share your screen' box in time.")
        if box["code"] != 0:
            raise RuntimeError("The 'share your screen' box was closed without choosing a window.")
        return box["results"]

    def opts(token, **extra):
        d = {"handle_token": GLib.Variant("s", token)}
        d.update(extra)
        return d

    say("step 1 of 3: asking the desktop for a sharing session...")
    res = call("CreateSession", lambda t: GLib.Variant("(a{sv})", (opts(t, session_handle_token=GLib.Variant("s", t + "s")),)))
    session = res["session_handle"]
    # types: 1 = monitor, 2 = window. cursor_mode 1 = hidden. persist_mode 2 = remember the choice, so the
    # "share your screen" box only asks the first time (the desktop hands back a token for next time).
    # Only ask for what this desktop says it can do (asking for more can make it refuse or never answer).
    types = (kinds & 3) if isinstance(kinds, int) and kinds & 3 else 3
    extra = dict(types=GLib.Variant("u", types), multiple=GLib.Variant("b", False))
    if isinstance(cursors, int) and cursors & 1:
        extra["cursor_mode"] = GLib.Variant("u", 1)          # hide the mouse pointer when the desktop allows it
    if isinstance(version, int) and version >= 4:
        extra["persist_mode"] = GLib.Variant("u", 2)
        saved = _load_token()
        if saved:
            extra["restore_token"] = GLib.Variant("s", saved)
    say("step 2 of 3: telling it what to share...")
    try:
        call("SelectSources", lambda t: GLib.Variant("(oa{sv})", (session, opts(t, **extra))))
    except RuntimeError as e:
        say(f"  the desktop said: {e}. Asking again the plain way...")
        for k in ("persist_mode", "restore_token", "cursor_mode"):
            extra.pop(k, None)
        call("SelectSources", lambda t: GLib.Variant("(oa{sv})", (session, opts(t, **extra))))
    say("step 3 of 3: the desktop now shows its 'share your screen' box. Waiting for your choice...")
    fired = []

    def nudge():
        fired.append(1)
        if on_slow:
            on_slow()
        return False
    slow = GLib.timeout_add_seconds(slow_after, nudge)
    try:
        res = call("Start", lambda t: GLib.Variant("(osa{sv})", (session, "", opts(t))))
    finally:
        if not fired:
            GLib.source_remove(slow)
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
