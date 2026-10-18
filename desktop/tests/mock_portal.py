"""A stand-in for xdg-desktop-portal's ScreenCast: same D-Bus names, methods and Response signals."""
import os, sys
from gi.repository import Gio, GLib
XML = """<node><interface name="org.freedesktop.portal.ScreenCast">
<method name="CreateSession"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="SelectSources"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="Start"><arg type="o" direction="in"/><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="OpenPipeWireRemote"><arg type="o" direction="in"/><arg type="a{sv}" direction="in"/><arg type="h" direction="out"/></method>
</interface></node>"""
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
    req = "/org/freedesktop/portal/desktop/request/%s/%s" % (sender.lstrip(":").replace(".", "_"), opts["handle_token"])
    inv.return_value(GLib.Variant("(o)", (req,)))
    if method == "CreateSession":
        res = {"session_handle": GLib.Variant("s", "/org/freedesktop/portal/desktop/session/x/" + opts["session_handle_token"])}; d = 0
    elif method == "SelectSources":
        res = {}; d = 0
    else:
        res = {"streams": GLib.Variant("a(ua{sv})", [(42, {})]), "restore_token": GLib.Variant("s", "tok-123")}; d = DELAY
    def emit():
        conn.emit_signal(sender, req, "org.freedesktop.portal.Request", "Response", GLib.Variant("(ua{sv})", (0, res)))
        return False
    GLib.timeout_add(d, emit)

bus = Gio.bus_get_sync(Gio.BusType.SESSION)
node = Gio.DBusNodeInfo.new_for_xml(XML)
bus.register_object("/org/freedesktop/portal/desktop", node.interfaces[0], handle, None, None)
Gio.bus_own_name_on_connection(bus, "org.freedesktop.portal.Desktop", Gio.BusNameOwnerFlags.NONE, None, None)
print("ready", flush=True)
GLib.MainLoop().run()
