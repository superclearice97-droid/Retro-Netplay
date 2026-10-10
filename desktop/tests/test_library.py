"""Game-folder scanning: names, systems, questions, and that the Java core names games exactly like Python."""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import library as L

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info else "")); ok or fails.append(name)

NAMES = ["Legend of Zelda, The - The Minish Cap (USA) [!].gba", "Urban_Reign_(USA).iso", "0042 - Pokemon - Emerald Version (U).gba",
         "Final Fantasy VII (USA) (Disc 1).cue", "Tekken.3.cue", "Soulcalibur (USA).gdi", "  Odd   spacing  - .gba",
         "A, The.gba", "Mario Kart - Double Dash!! (USA).rvz", "Pokémon Ruby (E).gba", "game.v1.2 (beta).iso"]

check("No-Intro article moved", L.clean_name(NAMES[0]) == "The Legend of Zelda - The Minish Cap", L.clean_name(NAMES[0]))
check("underscores and region removed", L.clean_name(NAMES[1]) == "Urban Reign")
check("set numbers removed", L.clean_name(NAMES[2]) == "Pokemon - Emerald Version")
check("disc tag removed", L.clean_name(NAMES[3]) == "Final Fantasy VII")
check("dots as spaces", L.clean_name(NAMES[4]) == "Tekken 3")
check("folder hint ps2", L.hint_for(["ROMs", "PlayStation 2"]) == "ps2")
check("folder hint wii -> gamecube", L.hint_for(["Games", "Wii"]) == "gamecube")
check("no hint from plain names", L.hint_for(["stuff", "downloads"]) is None)

gc = bytearray(0x9500); gc[0x1C:0x20] = b"\xC2\x33\x9F\x3D"
ps2 = bytearray(0x9500); ps2[0x8001:0x8006] = b"CD001"; ps2[0x8008:0x8013] = b"PLAYSTATION"
raw = bytearray(0x9500); raw[0x9319:0x931E] = b"CD001"; raw[0x9320:0x932B] = b"PLAYSTATION"
check("GameCube header", L.sniff(bytes(gc)) == "gamecube")
check("PS2 ISO volume", L.sniff(bytes(ps2)) == "ps2")
check("PS2 raw bin volume", L.sniff(bytes(raw)) == "ps2")
check("unknown iso", L.sniff(b"\0" * 0x9500) is None)

with tempfile.TemporaryDirectory() as d:
    def mk(rel, data=b"x"):
        p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(data); return p
    mk("Roms/GBA/Pokemon - Emerald Version (USA).gba")
    mk("Roms/GBA/Pokemon - Emerald Version (Europe).gba")
    mk("Roms/PS1/Crash Bandicoot (USA).cue"); mk("Roms/PS1/Crash Bandicoot (USA).bin")
    mk("Roms/PS1/Spyro (USA).chd")
    mk("Roms/Mixed/Urban Reign (USA).iso", bytes(ps2))
    mk("Roms/Mixed/Wind Waker (USA).iso", bytes(gc))
    mk("Roms/Mixed/Mystery (USA).chd")
    mk("Roms/.hidden/Secret.gba")
    games, qs = L.scan([os.path.join(d, "Roms")])
    names = {(g["system"], g["name"]) for g in games}
    check("finds GBA once (duplicates merged)", sum(1 for s, n in names if n == "Pokemon - Emerald Version") == 1, names)
    check("cue game found, its bin is not a separate game", ("ps1", "Crash Bandicoot") in names and not any(s == "ps2" for s, n in names if "Crash" in n))
    check("chd in PS1 folder is PS1", ("ps1", "Spyro") in names)
    check("iso sniffed as PS2", ("ps2", "Urban Reign") in names)
    check("iso sniffed as GameCube", ("gamecube", "Wind Waker") in names)
    check("hidden folders skipped", not any(n == "Secret" for s, n in names))
    check("unsure chd becomes one question", len(qs) == 1 and qs[0]["ext"] == ".chd" and qs[0]["count"] == 1, qs)
    games2, qs2 = L.scan([os.path.join(d, "Roms")], answers={qs[0]["key"]: "dreamcast"})
    check("answer applies", ("dreamcast", "Mystery") in {(g["system"], g["name"]) for g in games2} and not qs2)
    merged = L.merge([{"name": "My Hack", "system": "gba", "rom": "/x.gba"}, {"name": "Old", "system": "gba", "rom": "/o.gba", "auto": True}], games)
    check("merge keeps manual games, refreshes automatic ones", any(g["name"] == "My Hack" for g in merged) and not any(g["name"] == "Old" for g in merged))

# Java must name and hint exactly the same
root = os.path.join(os.path.dirname(os.path.dirname(HERE)), "android", "core", "src", "main", "java")
with tempfile.TemporaryDirectory() as out:
    src = os.path.join(out, "LibCheck.java")
    lines = "\n".join(f'        System.out.println(net.netplay.core.Library.cleanName("{n}"));' for n in NAMES)
    open(src, "w", encoding="utf-8").write("public class LibCheck { public static void main(String[] a) throws Exception {\n"
        "System.setOut(new java.io.PrintStream(System.out, true, \"UTF-8\"));\n" + lines +
        '\n        System.out.println(net.netplay.core.Library.hintFor(java.util.Arrays.asList("ROMs", "PlayStation 2")));'
        '\n        System.out.println(net.netplay.core.Library.pathFromDocId("primary:ROMs/GBA/x.gba"));'
        '\n        System.out.println(net.netplay.core.Library.pathFromDocId("1A2B-3C4D:Games"));\n} }\n')
    subprocess.run(["javac", "-encoding", "UTF-8", "-d", out, "-sourcepath", root, src], check=True)
    got = subprocess.run(["java", "-cp", out, "LibCheck"], capture_output=True, text=True, encoding="utf-8", check=True).stdout.splitlines()
    want = [L.clean_name(n) for n in NAMES] + ["ps2", "/storage/emulated/0/ROMs/GBA/x.gba", "/storage/1A2B-3C4D/Games"]
    diff = [(w, g) for w, g in zip(want, got) if w != g]
    check("Java and Python give identical names", not diff and len(got) == len(want), diff)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
