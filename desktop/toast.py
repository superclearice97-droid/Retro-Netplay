"""Small cards that pop up in a corner of the screen (above the game) to ask a quick question, for example
"Bea wants to join F-Zero GX: Accept / Decline". The ⇲ button moves them to the next corner; the app remembers it."""
import ink

CORNERS = ("top-right", "bottom-right", "bottom-left", "top-left")
MARGIN = 28
WIDTH = 380


class Toasts:
    def __init__(self, root, tk, fam, get_corner, set_corner):
        self.root, self.tk, self.fam = root, tk, fam
        self.get_corner, self.set_corner = get_corner, set_corner
        self.open = []          # [{"win", "actions", "key"}]

    def show(self, title, text, actions, timeout=60, key=None, on_timeout=None):
        """actions: [(label, callback, primary)]. The first primary action is what the controller hotkey does.
        key: replaces an open card with the same key (e.g. the same call)."""
        if key:
            self.close(key)
        tk = self.tk
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        outer = tk.Frame(win, bg=ink.INK, padx=2, pady=2)
        outer.pack(fill="both", expand=True)
        box = tk.Frame(outer, bg=ink.PAPER, padx=16, pady=12)
        box.pack(fill="both", expand=True)
        top = tk.Frame(box, bg=ink.PAPER)
        top.pack(fill="x")
        tk.Label(top, text=title.upper(), bg=ink.PAPER, fg=ink.INK, font=(self.fam, 9, "bold"), anchor="w").pack(side="left")
        entry = {"win": win, "actions": actions, "key": key, "job": None}
        move = tk.Label(top, text="⇲ move", bg=ink.PAPER, fg=ink.SOFT, font=(self.fam, 9), cursor="hand2")
        move.pack(side="right")
        move.bind("<Button-1>", lambda e: self.move())
        tk.Label(box, text=text, bg=ink.PAPER, fg=ink.INK, font=(self.fam, 11), wraplength=WIDTH - 40,
                 justify="left", anchor="w").pack(fill="x", pady=(6, 10))
        row = tk.Frame(box, bg=ink.PAPER)
        row.pack(fill="x")
        for label, cb, primary in actions:
            b = tk.Button(row, text=label, font=(self.fam, 10, "bold"), relief="flat", bd=0, padx=14, pady=6,
                          bg=ink.INK if primary else ink.PAPER, fg=ink.PAPER if primary else ink.INK,
                          activebackground="#333333" if primary else "#d8d7d2", highlightthickness=1,
                          highlightbackground=ink.INK, cursor="hand2",
                          command=lambda cb=cb: self._run(entry, cb))
            b.pack(side="left", padx=(0, 8))
        timer = tk.Label(box, text="", bg=ink.PAPER, fg=ink.SOFT, font=(self.fam, 9), anchor="w",
                         wraplength=WIDTH - 40, justify="left")
        timer.pack(fill="x", pady=(8, 0))
        primary = next((a for a in actions if a[2]), None)

        def tick(left):
            if not win.winfo_exists():
                return
            hint = f"Hold Start + Select to {primary[0].lower()}. " if primary else ""
            timer.config(text=f"{hint}Closes in {left} s")
            if left <= 0:
                self._close_entry(entry)
                if on_timeout:
                    on_timeout()
                return
            entry["job"] = win.after(1000, lambda: tick(left - 1))
        self.open.append(entry)
        self.place()
        win.deiconify()
        tick(timeout)
        try:
            self.root.bell()
        except tk.TclError:
            pass
        return entry

    def _run(self, entry, cb):
        self._close_entry(entry)
        cb()

    def _close_entry(self, entry):
        if entry in self.open:
            self.open.remove(entry)
        try:
            if entry["job"]:
                entry["win"].after_cancel(entry["job"])
            entry["win"].destroy()
        except self.tk.TclError:
            pass
        self.place()

    def close(self, key):
        for e in [e for e in self.open if e["key"] == key]:
            self._close_entry(e)

    def hotkey(self):
        """Controller hotkey while a card is open: do its main action (e.g. Accept). True if it was used."""
        for e in reversed(self.open):
            act = next((a for a in e["actions"] if a[2]), None)
            if act:
                self._run(e, act[1])
                return True
        return False

    def move(self):
        cur = self.get_corner()
        nxt = CORNERS[(CORNERS.index(cur) + 1) % len(CORNERS)] if cur in CORNERS else CORNERS[0]
        self.set_corner(nxt)
        self.place()

    def place(self):
        corner = self.get_corner() if self.get_corner() in CORNERS else CORNERS[0]
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        y_off = MARGIN
        for e in self.open:
            w = e["win"]
            try:
                w.update_idletasks()
                ww, wh = max(WIDTH, w.winfo_reqwidth()), w.winfo_reqheight()
                x = sw - ww - MARGIN if corner.endswith("right") else MARGIN
                y = y_off if corner.startswith("top") else sh - wh - y_off
                w.geometry(f"{ww}x{wh}+{x}+{y}")
                y_off += wh + 12
            except self.tk.TclError:
                pass
