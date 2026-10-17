"""First-run setup: games folders, emulators, online, controllers. Shown once; can be run again from Settings.

Everything here only changes the app's own settings. Scanning reads file names (and the first few KB of
.iso/.bin files to tell PS2 from GameCube) on this computer; nothing is uploaded.
"""
import os
import threading

import ink
import library


def run(root, tk, ttk, filedialog, cfg, ctx, start_page=0, only=None):
    """ctx: dict of callbacks from app.py: autodetect, emu_ok, EMUS, SYSTEMS, refresh_ports, controller_check, done."""
    win = tk.Toplevel(root)
    win.title("Set up Retro-Netplay")
    win.transient(root)
    win.geometry("560x560")
    win.minsize(520, 520)
    fam = ink.mono_family(root)
    head = ttk.Frame(win, padding=(20, 18, 20, 4))
    head.pack(fill="x")
    step_lbl = ttk.Label(head, text="", font=(fam, 9))
    step_lbl.pack(anchor="w")
    title = ttk.Label(head, text="", font=(fam, 15, "bold"))
    title.pack(anchor="w", pady=(4, 0))
    body = ttk.Frame(win, padding=(20, 8, 20, 8))
    body.pack(fill="both", expand=True)
    bar = ttk.Frame(win, padding=(20, 8, 20, 16))
    bar.pack(fill="x", side="bottom")
    back_btn = ttk.Button(bar, text="Back")
    next_btn = ttk.Button(bar, text="Next")
    back_btn.pack(side="left")
    next_btn.pack(side="right")

    pages = [games_page, emulators_page, online_page, controllers_page]
    if only is not None:
        pages = [pages[only]]
        start_page = 0
    state = {"i": start_page}

    def show(i):
        state["i"] = i
        for w in body.winfo_children():
            w.destroy()
        name, builder = pages[i](body, tk, ttk, filedialog, cfg, ctx, fam, win)
        title.config(text=name)
        step_lbl.config(text=f"STEP {i + 1} OF {len(pages)}" if len(pages) > 1 else "")
        back_btn.config(state="normal" if i > 0 else "disabled", command=lambda: show(i - 1))
        last = i == len(pages) - 1
        next_btn.config(text="Finish" if last else "Next", command=(finish if last else lambda: show(i + 1)))

    def finish():
        if only is None:
            cfg["setup_done"] = True
        win.destroy()
        ctx["done"]()

    win.protocol("WM_DELETE_WINDOW", finish)
    show(state["i"])
    return win


# ------------------------------------------------------------------ step 1: games
def rescan(cfg):
    games, questions = library.scan(cfg.get("game_folders", []), cfg.get("folder_answers", {}))
    cfg["games"] = library.merge(cfg.get("games", []), games)
    return games, questions


def games_page(body, tk, ttk, filedialog, cfg, ctx, fam, win):
    ttk.Label(body, wraplength=500, justify="left", text=(
        "Pick the folders where you keep your games. Every game inside, including subfolders, is added "
        "and named automatically, so you never add games one at a time. New games show up the next time "
        "you open the app.")).pack(anchor="w")
    lst = tk.Listbox(body, height=4, font=(fam, 10), bg="#efeeea", fg=ink.INK, relief="solid", borderwidth=1,
                     highlightthickness=0, selectbackground=ink.INK, selectforeground=ink.PAPER)
    lst.pack(fill="x", pady=(10, 6))
    row = ttk.Frame(body)
    row.pack(fill="x")
    summary = ttk.Label(body, text="", wraplength=500, justify="left")
    summary.pack(anchor="w", pady=(10, 4))
    qbox = ttk.Frame(body)
    qbox.pack(fill="x")

    def fill():
        lst.delete(0, "end")
        for f in cfg.get("game_folders", []):
            lst.insert("end", f)

    def scan_now():
        summary.config(text="Looking for games...")
        def work():
            games, questions = rescan(cfg)
            win.after(0, lambda: show_result(games, questions))
        threading.Thread(target=work, daemon=True).start()

    def show_result(games, questions):
        if not win.winfo_exists():
            return
        counts = {}
        for g in games:
            counts[g["system"]] = counts.get(g["system"], 0) + 1
        systems = ctx["SYSTEMS"]
        if games:
            text = f"Found {len(games)} game{'s' if len(games) != 1 else ''}: " + ", ".join(
                f"{n} {systems[s]['label']}" for s, n in sorted(counts.items(), key=lambda x: -x[1]))
        else:
            text = "No games found yet." if cfg.get("game_folders") else "Add a folder to get started."
        summary.config(text=text)
        for w in qbox.winfo_children():
            w.destroy()
        if questions:
            ttk.Label(qbox, wraplength=500, justify="left", text=(
                "Some files could be for more than one system. Tell me once per folder:")).pack(anchor="w", pady=(6, 4))
        for q in questions[:8]:
            r = ttk.Frame(qbox)
            r.pack(fill="x", pady=2)
            label = f"{q['count']} {q['ext']} file{'s' if q['count'] != 1 else ''} in {os.path.basename(q['folder']) or q['folder']}"
            ttk.Label(r, text=label, width=34).pack(side="left")
            names = [systems[c]["label"] for c in q["candidates"]] + ["Skip these"]
            v = tk.StringVar(value="Choose...")
            cb = ttk.Combobox(r, textvariable=v, values=names, state="readonly", width=20)
            cb.pack(side="left")

            def picked(_e=None, q=q, v=v):
                choice = v.get()
                ans = "skip" if choice == "Skip these" else next(c for c in q["candidates"] if systems[c]["label"] == choice)
                cfg.setdefault("folder_answers", {})[q["key"]] = ans
                scan_now()
            cb.bind("<<ComboboxSelected>>", picked)

    def add():
        p = filedialog.askdirectory(title="Choose a folder with games", parent=win)
        if p and p not in cfg.setdefault("game_folders", []):
            cfg["game_folders"].append(p)
            fill()
            scan_now()

    def remove():
        sel = lst.curselection()
        if sel:
            cfg["game_folders"].pop(sel[0])
            fill()
            scan_now()

    ttk.Button(row, text="Add a folder...", command=add).pack(side="left")
    ttk.Button(row, text="Remove folder", command=remove).pack(side="left", padx=(6, 0))
    ttk.Button(row, text="Scan again", command=scan_now).pack(side="right")
    fill()
    scan_now() if cfg.get("game_folders") else summary.config(text="Add a folder to get started.")
    return "Where are your games?", None


# ------------------------------------------------------------------ step 2: emulators
def emulators_page(body, tk, ttk, filedialog, cfg, ctx, fam, win):
    ctx["autodetect"](cfg)
    ttk.Label(body, wraplength=500, justify="left", text=(
        "These are the programs that run the games. I looked for them on this computer. You only need the ones "
        "for the systems you play.")).pack(anchor="w", pady=(0, 10))
    rows = ttk.Frame(body)
    rows.pack(fill="x")

    def draw():
        for w in rows.winfo_children():
            w.destroy()
        for key, (label, _names, _globs) in ctx["EMUS"].items():
            r = ttk.Frame(rows)
            r.pack(fill="x", pady=3)
            ok = ctx["emu_ok"](cfg["paths"].get(key, ""))
            tk.Label(r, text="OK" if ok else "--", width=4, bg=ink.spectrum(0.30 if ok else 0.04), fg=ink.INK,
                     font=(fam, 9, "bold")).pack(side="left", padx=(0, 10))
            ttk.Label(r, text=label, width=12).pack(side="left")
            ttk.Label(r, text="found" if ok else "not found", width=12).pack(side="left")

            def pick(key=key):
                p = filedialog.askopenfilename(title=f"Find {ctx['EMUS'][key][0]}", parent=win)
                if p:
                    cfg["paths"][key] = p
                    draw()
            ttk.Button(r, text="Find it..." if not ok else "Change...", command=pick).pack(side="right")
    draw()
    ttk.Label(body, wraplength=500, justify="left", foreground=ink.SOFT, text=(
        "Don't have one? Get it from its official site: RetroArch (retroarch.com) for Game Boy Advance, "
        "PS1 and Dreamcast; Dolphin (dolphin-emu.org) for GameCube and Wii; PCSX2 (pcsx2.net) for PS2; "
        "RPCS3 (rpcs3.net) for PS3. Then press Next and Back to look again.")).pack(anchor="w", pady=(14, 0))
    return "The emulators", None


# ------------------------------------------------------------------ step 3: online
def online_page(body, tk, ttk, filedialog, cfg, ctx, fam, win):
    import lan
    ttk.Label(body, wraplength=500, justify="left", text=(
        "Players find each other through a matchmaker. At home there's nothing to set up: Retro-Netplay on this "
        "computer and on your phones find each other on the Wi-Fi by themselves.")).pack(anchor="w")
    typed = str(cfg.get("server_host", "")).strip()
    internet = typed and not lan.is_loopback(typed) and not lan.is_private(typed)
    mode = tk.StringVar(value="friend" if internet else "run")
    host = tk.StringVar(value=typed if internet else "")
    f1 = ttk.Frame(body)
    f1.pack(fill="x", pady=(10, 0))
    ttk.Radiobutton(f1, text="Automatic (recommended)", variable=mode, value="run").pack(anchor="w")
    ttk.Label(f1, wraplength=470, justify="left", foreground=ink.SOFT, text=(
        "Phones and computers on your Wi-Fi connect by themselves. Nothing to type.")).pack(anchor="w", padx=(24, 0), pady=(0, 8))
    ttk.Radiobutton(f1, text="A friend runs a matchmaker on the internet. Their address:", variable=mode, value="friend").pack(anchor="w")
    erow = ttk.Frame(f1)
    erow.pack(fill="x", padx=(24, 0), pady=(2, 2))
    e = ttk.Entry(erow, textvariable=host)
    e.pack(side="left", fill="x", expand=True)
    e.bind("<FocusIn>", lambda _e: mode.set("friend"))
    ttk.Label(f1, wraplength=470, justify="left", foreground=ink.SOFT, text=(
        "Only if they gave you one. Leave it on Automatic otherwise.")).pack(anchor="w", padx=(24, 0))

    auto = tk.BooleanVar(value=cfg.get("auto_ports", True))
    ttk.Checkbutton(body, variable=auto, text="Open ports on my router automatically (recommended)").pack(anchor="w", pady=(16, 2))
    ttk.Label(body, wraplength=500, justify="left", foreground=ink.SOFT, text=(
        "Hosting a game needs your router to let other players in. With this on, the app asks your router to open "
        "only the ports it needs (UPnP or NAT-PMP), keeps them open while the app runs, and closes them when you "
        "quit. Joining other people works either way.")).pack(anchor="w")
    result = ttk.Label(body, text="", wraplength=500, justify="left")
    test = ttk.Button(body, text="Test my connection")
    test.pack(anchor="w", pady=(12, 4))
    result.pack(anchor="w")

    def save():
        cfg["auto_ports"] = auto.get()
        h = host.get().strip()
        if mode.get() == "run" or not h:
            cfg["run_server"], cfg["auto_mm"] = True, True
            if not lan.is_private(str(cfg.get("server_host", ""))):
                cfg["server_host"] = "127.0.0.1"
        else:
            a = lan.parse_address(h)
            if a:
                cfg["server_host"], cfg["server_port"], cfg["auto_mm"] = a[0], a[1], lan.is_private(a[0])

    def run_test():
        save()
        result.config(text="Asking your router and the matchmaker...")
        test.config(state="disabled")
        def work():
            out = ctx["refresh_ports"](cfg)
            lines = [v["message"] for v in out.values()] or ["Nothing to open: no emulator that hosts netplay is set up yet."]
            win.after(0, lambda: (result.config(text="\n".join(lines)), test.config(state="normal")) if win.winfo_exists() else None)
        threading.Thread(target=work, daemon=True).start()

    test.config(command=run_test)
    for v in (mode, host, auto):
        v.trace_add("write", lambda *_: save())
    return "Playing online", None


# ------------------------------------------------------------------ step 4: controllers
def controllers_page(body, tk, ttk, filedialog, cfg, ctx, fam, win):
    ttk.Label(body, wraplength=500, justify="left", text=(
        "Last step. Connect your controllers now (two if you play side by side). Player 1 and Player 2 are always "
        "on; you can map buttons any time from Controllers on the main window.")).pack(anchor="w")
    ttk.Button(body, text="Check my controllers", command=ctx["controller_check"]).pack(anchor="w", pady=(14, 0))
    ttk.Label(body, wraplength=500, justify="left", text=(
        "In any game, hold START + SELECT for 3 seconds to join the queue, and again to leave it. "
        "(Change the buttons with \"Set controller hotkey\" on the main window.)")).pack(anchor="w", pady=(14, 0))
    if os.name == "posix" and not os.uname().sysname == "Darwin":
        menu = tk.BooleanVar(value=cfg.get("menu_entry", True))
        def keep():
            cfg["menu_entry"] = menu.get()
        ttk.Checkbutton(body, text="Add Retro-Netplay to my app menu (and open invite links with it)",
                        variable=menu, command=keep).pack(anchor="w", pady=(14, 0))
        keep()
    ttk.Label(body, wraplength=500, justify="left", foreground=ink.SOFT, text=(
        "You can run this setup again any time from Settings.")).pack(anchor="w", pady=(18, 0))
    return "Controllers", None
