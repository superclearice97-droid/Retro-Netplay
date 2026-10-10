"""Player slots and controller layouts. Pure logic: no GUI, no pygame.

A slot is one player seat:
  enabled : Player 1 and 2 are ALWAYS on (a second player can join any time).
  device  : None = automatic (slot N uses the Nth connected controller) or a controller number (0-based).
  binds   : None = leave the emulator's own controller auto-detection alone,
            or {action: token} once the player has mapped their buttons.
Tokens: "b3" = button 3, "h0up" = d-pad (hat 0) up, "a+4" = axis 4 pushed positive.
"""
import copy

MAX_SLOTS = 4
ALWAYS_ON = 2  # slots 0 and 1 (Player 1 and Player 2)

# (RetroArch action name, friendly label)
ACTIONS = [
    ("up", "D-pad UP"), ("down", "D-pad DOWN"), ("left", "D-pad LEFT"), ("right", "D-pad RIGHT"),
    ("b", "the BOTTOM face button (A on Xbox, X on PlayStation)"),
    ("a", "the RIGHT face button (B on Xbox, O on PlayStation)"),
    ("y", "the LEFT face button (X on Xbox, square on PlayStation)"),
    ("x", "the TOP face button (Y on Xbox, triangle on PlayStation)"),
    ("l", "the LEFT bumper (L1 / LB)"), ("r", "the RIGHT bumper (R1 / RB)"),
    ("l2", "the LEFT trigger (L2 / LT)"), ("r2", "the RIGHT trigger (R2 / RT)"),
    ("select", "SELECT (Back / Share)"), ("start", "START (Menu / Options)"),
]


def default_slots():
    return [{"enabled": i < ALWAYS_ON, "device": None, "binds": None} for i in range(MAX_SLOTS)]


def normalize(slots):
    """Make any saved value a valid list of 4 slots with P1 and P2 forced on."""
    out = default_slots()
    for i, s in enumerate(slots[:MAX_SLOTS] if isinstance(slots, list) else []):
        if isinstance(s, dict):
            out[i]["enabled"] = bool(s.get("enabled", out[i]["enabled"]))
            dev = s.get("device")
            out[i]["device"] = dev if isinstance(dev, int) and dev >= 0 else None
            b = s.get("binds")
            out[i]["binds"] = dict(b) if isinstance(b, dict) and b else None
    for i in range(ALWAYS_ON):
        out[i]["enabled"] = True
    return out


def set_enabled(slots, i, value):
    """Players 1 and 2 cannot be switched off."""
    if i < ALWAYS_ON:
        slots[i]["enabled"] = True
    else:
        slots[i]["enabled"] = bool(value)


def device_for(slots, i):
    d = slots[i]["device"]
    return i if d is None else d


def copy_binds(slots, src, dst):
    """Paste src's button layout onto dst. The physical controller choice is NOT copied."""
    if src != dst:
        slots[dst]["binds"] = copy.deepcopy(slots[src]["binds"])


def copy_to_all(slots, src):
    for i in range(len(slots)):
        if i != src and slots[i]["enabled"]:
            copy_binds(slots, src, i)


def ready(slots, i, n_connected):
    """True if this slot's controller is plugged in right now."""
    return slots[i]["enabled"] and device_for(slots, i) < n_connected


# ----------------------------------------------------------------- learning a button press
def is_idle(state):
    return not state["buttons"] and all(h == (0, 0) for h in state["hats"])


def detect_input(state, base_axes, threshold=0.6):
    """state = {"buttons": set, "hats": [(x,y)], "axes": [float]}. Returns a token or None."""
    if state["buttons"]:
        return f"b{min(state['buttons'])}"
    for n, (x, y) in enumerate(state["hats"]):
        for cond, name in ((y == 1, "up"), (y == -1, "down"), (x == -1, "left"), (x == 1, "right")):
            if cond:
                return f"h{n}{name}"
    for n, v in enumerate(state["axes"]):
        base = base_axes[n] if n < len(base_axes) else 0.0
        if v - base > threshold:
            return f"a+{n}"
        if base - v > threshold:
            return f"a-{n}"
    return None


# ----------------------------------------------------------------- RetroArch
def ra_line(player, action, token):
    kind = "axis" if token.startswith("a") else "btn"
    value = token[1:] if token.startswith("b") else (token[1:] if token.startswith("a") else token)
    return f'input_player{player}_{action}_{kind} = "{value}"'


def retroarch_config(slots):
    """Text for RetroArch's --appendconfig. Only touches what the user set:
    controller order for every enabled slot, buttons only for slots they mapped."""
    lines = [f'input_max_users = "{MAX_SLOTS}"']
    for i, s in enumerate(slots):
        if not s["enabled"]:
            continue
        p = i + 1
        lines.append(f'input_player{p}_joypad_index = "{device_for(slots, i)}"')
        for action, _ in ACTIONS:
            tok = (s["binds"] or {}).get(action)
            if tok:
                lines.append(ra_line(p, action, tok))
    return "\n".join(lines) + "\n"
