"""A stand-in for xdg-desktop-portal's ScreenCast: same D-Bus names, methods and Response signals."""
import os, sys
from gi.repository import Gio, GLib
XML = """<node><interface name="org.freedesktop.portal.ScreenCast">
<method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="SelectSources"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="Start"><arg type="o" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="OpenPipeWireRemote"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="h" direction="out"/></method>
<property name="version" type="u" access="read"/>
<property name="AvailableSourceTypes" type="u" access="read"/>
<property name="AvailableCursorModes" type="u" access="read"/>
</interface></node>"""
PROPS = {"version": GLib.Variant("u", int(os.environ.get("MOCK_VERSION", "4"))), "AvailableSourceTypes": GLib.Variant("u", 3),
         "AvailableCursorModes": GLib.Variant("u", int(os.environ.get("MOCK_CURSORS", "7")))}
REQ = Gio.DBusNodeInfo.new_for_xml('<node><interface name="org.freedesktop.portal.Request"><method name="Close"/></interface></node>')
SES = Gio.DBusNodeInfo.new_for_xml('<node><interface name="org.freedesktop.portal.Session"><method name="Close"/>'
                                   '<property name="version" type="u" access="read"/></interface></node>')
SESSIONS = {}   # app (sender) -> [(session path, registration id)]: like the real portal, a session ends with its app


def end_sessions(conn, sender, why):
    for path, reg in SESSIONS.pop(sender, []):
        conn.unregister_object(reg)
        print("session closed:", why, file=log, flush=True)


def owner_changed(conn, _s, _p, _i, _sig, params):
    name, _old, new = params.unpack()
    if not new and name in SESSIONS:
        end_sessions(conn, name, "its app left the bus")
DELAY = int(os.environ.get("PICK_DELAY_MS", "300"))
log = open(os.environ.get("MOCK_LOG", "/dev/null"), "a")

def handle(conn, sender, path, iface, method, params, inv):
    args = params.unpack()
    print("call", method, {k: v for k, v in (args[-1] if isinstance(args[-1], dict) else {}).items()}, file=log, flush=True)
    if method == "OpenPipeWireRemote":
        r, w = os.pipe()
        fl = Gio.UnixFDList.new(); idx = fl.append(r)
        inv.return_value_with_unix_fd_list(GLib.Variant("(h)", (idx,)), fl); return
    opts = args[-1]
    if method == "SelectSources" and os.environ.get("MOCK_REFUSE_EXTRAS") and "persist_mode" in opts:
        inv.return_dbus_error("org.freedesktop.portal.Error.InvalidArgument", "Unknown option persist_mode"); return
    req = "/org/freedesktop/portal/desktop/request/%s/%s" % (sender.lstrip(":").replace(".", "_"), opts["handle_token"])
    inv.return_value(GLib.Variant("(o)", (req,)))
    try:      # the request object, so Close (withdraw) can be seen
        conn.register_object(req, REQ.interfaces[0], lambda *a: (print("call Close", a[2], file=log, flush=True),
                                                                   a[-1].return_value(None)), None, None)
    except GLib.Error:
        pass
    if method == "Start" and os.environ.get("MOCK_STUCK_FILE") and not os.path.exists(os.environ["MOCK_STUCK_FILE"]):
        open(os.environ["MOCK_STUCK_FILE"], "w").close()      # the first box is "stuck": never answered
        return
    if method == "CreateSession":
        spath = "/org/freedesktop/portal/desktop/session/x/" + opts["session_handle_token"]
        reg = conn.register_object(spath, SES.interfaces[0],
                                   lambda c, snd, *a: (end_sessions(c, snd, "the app closed it"), a[-1].return_value(None)),
                                   lambda *a: GLib.Variant("u", 1), None)
        SESSIONS.setdefault(sender, []).append((spath, reg))
        res = {"session_handle": GLib.Variant("s", spath)}; d = 0
    elif method == "SelectSources":
        res = {}; d = 0
    else:
        res = {"streams": GLib.Variant("a(ua{sv})", [(42, {})]), "restore_token": GLib.Variant("s", "tok-123")}; d = DELAY
    def emit():
        conn.emit_signal(sender, req, "org.freedesktop.portal.Request", "Response", GLib.Variant("(ua{sv})", (0, res)))
        return False
    GLib.timeout_add(d, emit)

bus = Gio.bus_get_sync(Gio.BusType.SESSION)
bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged", "/org/freedesktop/DBus", None,
                     Gio.DBusSignalFlags.NONE, owner_changed)
node = Gio.DBusNodeInfo.new_for_xml(XML)
bus.register_object("/org/freedesktop/portal/desktop", node.interfaces[0], handle,
                    lambda conn, sender, path, iface, name: PROPS[name], None)
Gio.bus_own_name_on_connection(bus, "org.freedesktop.portal.Desktop", Gio.BusNameOwnerFlags.NONE, None, None)
print("ready", flush=True)
GLib.MainLoop().run()
