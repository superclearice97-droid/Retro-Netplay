"""A virtual game controller for the host (Linux uinput), driven by the guest's input packets.

The guest's player sends 16-byte packets (see PROTOCOL.md, Media and controller). We turn them into evdev events on a
virtual pad that identifies itself as an Xbox 360 controller, so SDL, RetroArch, Dolphin and PCSX2
all recognise it with their normal built-in mappings. Pure packing code is testable anywhere;
only UinputPad.open() needs /dev/uinput.
"""
import os
import struct
import time

# ---- linux/input-event-codes.h
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x11
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR = 0x136, 0x137, 0x13A, 0x13B, 0x13C, 0x13D, 0x13E
# ---- linux/uinput.h
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567

PACKET = struct.Struct("<BBI4h2B")      # ver, seq, buttons, lx ly rx ry, lt rt  (16 bytes)

# W3C "standard gamepad" button number -> evdev key. 6/7 (triggers) and 12-15 (d-pad) are axes.
BUTTON_KEYS = {0: BTN_SOUTH, 1: BTN_EAST, 2: BTN_WEST, 3: BTN_NORTH, 4: BTN_TL, 5: BTN_TR,
               8: BTN_SELECT, 9: BTN_START, 10: BTN_THUMBL, 11: BTN_THUMBR, 16: BTN_MODE}
ABS_SETUP = [  # code, min, max
    (ABS_X, -32768, 32767), (ABS_Y, -32768, 32767), (ABS_RX, -32768, 32767), (ABS_RY, -32768, 32767),
    (ABS_Z, 0, 255), (ABS_RZ, 0, 255), (ABS_HAT0X, -1, 1), (ABS_HAT0Y, -1, 1)]


def parse_packet(data):
    """16 bytes from the guest -> state dict, or None if it isn't a valid v1 packet."""
    if not isinstance(data, (bytes, bytearray)) or len(data) != PACKET.size:
        return None
    ver, seq, buttons, lx, ly, rx, ry, lt, rt = PACKET.unpack(bytes(data))
    if ver != 1:
        return None
    return {"seq": seq, "buttons": buttons, "ax": (lx, ly, rx, ry), "lt": lt, "rt": rt}


def neutral():
    return {"seq": 0, "buttons": 0, "ax": (0, 0, 0, 0), "lt": 0, "rt": 0}


def _hat(buttons, neg_bit, pos_bit):
    return (1 if buttons >> pos_bit & 1 else 0) - (1 if buttons >> neg_bit & 1 else 0)


def diff_events(prev, cur):
    """Events needed to go from prev to cur: [(type, code, value)], ending with a SYN when non-empty."""
    ev = []
    for bit, code in BUTTON_KEYS.items():
        a, b = prev["buttons"] >> bit & 1, cur["buttons"] >> bit & 1
        if a != b:
            ev.append((EV_KEY, code, b))
    for code, a, b in ((ABS_HAT0X, _hat(prev["buttons"], 14, 15), _hat(cur["buttons"], 14, 15)),
                       (ABS_HAT0Y, _hat(prev["buttons"], 12, 13), _hat(cur["buttons"], 12, 13))):
        if a != b:
            ev.append((EV_ABS, code, b))
    for code, a, b in zip((ABS_X, ABS_Y, ABS_RX, ABS_RY), prev["ax"], cur["ax"]):
        if a != b:
            ev.append((EV_ABS, code, b))
    for code, a, b in ((ABS_Z, prev["lt"], cur["lt"]), (ABS_RZ, prev["rt"], cur["rt"])):
        if a != b:
            ev.append((EV_ABS, code, b))
    if ev:
        ev.append((EV_SYN, 0, 0))
    return ev


def pack_event(etype, code, value, now=None):
    t = time.time() if now is None else now
    return struct.pack("@llHHi", int(t), int((t % 1) * 1e6), etype, code, value)


def user_dev(name, vendor=0x045E, product=0x028E, version=0x0110):
    """The legacy uinput_user_dev block: name, ids, then per-axis max/min/fuzz/flat tables."""
    absmax, absmin, absfuzz, absflat = [0] * 64, [0] * 64, [0] * 64, [0] * 64
    for code, lo, hi in ABS_SETUP:
        absmin[code], absmax[code] = lo, hi
        absflat[code] = 4096 if hi > 255 else 0
    return struct.pack("<80s4HI" + "64i" * 4, name.encode()[:79], 0x03, vendor, product, version, 0,
                       *absmax, *absmin, *absfuzz, *absflat)


class UinputPad:
    def __init__(self, name="Netplay Virtual Pad"):
        self.name, self.fd, self.state = name, None, neutral()

    def open(self):
        import fcntl      # Linux only; imported here so other systems can still import this module
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
            for code in BUTTON_KEYS.values():
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            for code, _, _ in ABS_SETUP:
                fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            os.write(self.fd, user_dev(self.name))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except Exception:
            self.close()
            raise

    def apply(self, cur):
        ev = diff_events(self.state, cur)
        if ev and self.fd is not None:
            now = time.time()
            os.write(self.fd, b"".join(pack_event(t, c, v, now) for t, c, v in ev))
        self.state = cur

    def release_all(self):
        self.apply(neutral())

    def close(self):
        if self.fd is not None:
            try:
                self.release_all()
                import fcntl
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = None


def uinput_problem():
    """Plain-language reason /dev/uinput can't be used, or '' if it can."""
    if not os.path.exists("/dev/uinput"):
        return "/dev/uinput is missing. Try: sudo modprobe uinput"
    if not os.access("/dev/uinput", os.W_OK):
        return ("No permission for /dev/uinput. One-time fix:\n"
                "  echo 'KERNEL==\"uinput\", MODE=\"0660\", GROUP=\"input\", OPTIONS+=\"static_node=uinput\"' | "
                "sudo tee /etc/udev/rules.d/99-netplay-uinput.rules\n"
                "  sudo usermod -aG input $USER   (then log out and back in)")
    return ""
