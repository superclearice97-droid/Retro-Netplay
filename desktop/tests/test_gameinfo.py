"""Game art and details from what Skraper / EmulationStation / ES-DE / RetroArch left on disk: gamelist.xml,
media folders, Batocera-style names, ES-DE's downloaded_media and RetroArch thumbnails. And the Java core
(GameInfo.java, used by the phone) gives exactly the same answers as Python."""
import json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{str(info)[:1500]}]" if info and not ok else "")); ok or fails.append(name)

home = tempfile.mkdtemp()
os.environ["HOME"] = home
import gameinfo as G

def touch(*parts, data=b"x"):
    p = os.path.join(home, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
    return p

ZELDA = "Legend of Zelda, The - The Minish Cap (USA)"
GAMELIST = f"""<?xml version="1.0"?>
<!DOCTYPE gameList [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<gameList>
  <folder><path>./sub</path><name>Sub folder</name></folder>
  <game id="1" source="ScreenScraper.fr">
    <path>./{ZELDA.replace('&', '&amp;')}.gba</path>
    <name>The Legend of Zelda: The Minish Cap</name>
    <desc>Link shrinks &amp; grows. &#x2764; &xxe; <![CDATA[<b>bold</b> & more]]></desc>
    <image>./media/images/{ZELDA}.png</image>
    <thumbnail>./media/box2dfront/{ZELDA}.png</thumbnail>
    <video>./media/videos/{ZELDA}.mp4</video>
    <marquee>./media/wheel/{ZELDA}.png</marquee>
    <missing>./media/fanart/does-not-exist.png</missing>
    <fanart>./media/fanart/nope.png</fanart>
    <rating>0.8</rating>
    <releasedate>20041104T000000</releasedate>
    <developer>Capcom</developer>
    <publisher>Nintendo</publisher>
    <genre>Action / Adventure</genre>
    <players>1</players>
  </game>
  <game><path>./Other Game.gba</path><name>Other</name><rating>nan</rating><releasedate>1066</releasedate></game>
</gameList>"""
touch("roms", "gba", "gamelist.xml", data=GAMELIST.encode())
zrom = touch("roms", "gba", ZELDA + ".gba")
for sub, ext in [("images", "png"), ("box2dfront", "png"), ("videos", "mp4"), ("wheel", "png"), ("titlescreens", "png")]:
    touch("roms", "gba", "media", sub, f"{ZELDA}.{ext}")

# Batocera / Skraper "-suffix" names, no gamelist
mrom = touch("roms", "gba", "Metroid Fusion (USA).gba")
touch("roms", "gba", "images", "Metroid Fusion (USA)-thumb.png")
touch("roms", "gba", "images", "Metroid Fusion (USA)-image.png")
touch("roms", "gba", "images", "Metroid Fusion (USA)-marquee.png")
touch("roms", "gba", "videos", "Metroid Fusion (USA)-video.mp4")
touch("roms", "gba", "videos", "Metroid Fusion (USA)-video.png")     # an image in a video slot: ignored

# ES-DE's downloaded_media, and RetroArch thumbnails (also for a streamed PS2 game that has no file here)
touch("ES-DE", "downloaded_media", "psx", "covers", "Crash Bandicoot (USA).png")
touch("ES-DE", "downloaded_media", "psx", "screenshots", "Crash Bandicoot (USA).jpg")
touch("ES-DE", "downloaded_media", "psx", "videos", "Crash Bandicoot (USA).mp4")
crom = touch("roms", "psx", "Crash Bandicoot (USA).cue")
touch(".config", "retroarch", "thumbnails", "Sony - PlayStation 2", "Named_Boxarts", "Urban Reign.png")
touch(".config", "retroarch", "thumbnails", "Sony - PlayStation 2", "Named_Snaps", "Urban Reign.png")
touch(".config", "retroarch", "thumbnails", "Nintendo - Game Boy Advance", "Named_Titles", "Metroid Fusion (USA).png")

F = G.Finder()
z = F.find({"name": "The Legend of Zelda - The Minish Cap", "system": "gba", "rom": zrom})
check("gamelist: name, developer, publisher, genre, players", (z["name"], z["developer"], z["publisher"], z["genre"], z["players"]) ==
      ("The Legend of Zelda: The Minish Cap", "Capcom", "Nintendo", "Action / Adventure", "1"), z)
check("gamelist: year from releasedate, rating", (z["year"], z["rating"]) == ("2004", "0.80"), z)
check("gamelist: entities, CDATA; DTD entities are NOT expanded",
      z["desc"] == "Link shrinks & grows. \u2764 &xxe; <b>bold</b> & more", z["desc"])
check("gamelist: box art from <thumbnail>", z["box"].endswith(os.path.join("box2dfront", ZELDA + ".png")), z["box"])
check("gamelist: screenshot, video, logo", z["screens"] and z["screens"][0].endswith(os.path.join("images", ZELDA + ".png"))
      and z["video"].endswith(".mp4") and z["logo"].endswith(os.path.join("wheel", ZELDA + ".png")), z)
check("media folder fills what the gamelist lacks (title screen)", z["title"].endswith(os.path.join("titlescreens", ZELDA + ".png")), z["title"])
check("missing files are left out", z["fanart"] == "", z["fanart"])

m = F.find({"name": "Metroid Fusion", "system": "gba", "rom": mrom})
check("Batocera names: -thumb is the box, -image a screenshot, -marquee the logo",
      m["box"].endswith("Metroid Fusion (USA)-thumb.png") and m["screens"][0].endswith("Metroid Fusion (USA)-image.png")
      and m["logo"].endswith("-marquee.png"), m)
check("Batocera names: -video.mp4 is the clip, an image named -video is not", m["video"].endswith("Metroid Fusion (USA)-video.mp4"), m["video"])
check("RetroArch title screen found by the file's name", m["title"].endswith(os.path.join("Named_Titles", "Metroid Fusion (USA).png")), m["title"])
check("no gamelist: the name is the app's", m["name"] == "Metroid Fusion" and m["desc"] == "", m)

c = F.find({"name": "Crash Bandicoot", "system": "ps1", "rom": crom})
check("ES-DE downloaded_media (psx folder for PS1)", c["box"].endswith(os.path.join("covers", "Crash Bandicoot (USA).png"))
      and c["screens"] and c["video"].endswith(".mp4"), c)

u = F.find({"name": "Urban Reign", "system": "ps2", "rom": ""})
check("streamed game with no file: RetroArch thumbnails by name", u["box"].endswith("Urban Reign.png") and len(u["screens"]) == 1, u)
n = F.find({"name": "Nothing Here", "system": "gamecube", "rom": ""})
check("nothing found: empty info, never an error", n["box"] == "" and n["screens"] == [] and n["name"] == "Nothing Here", n)

check("media_kind", [G.media_kind(p) for p in ["media/box2dfront/A.png", "images/A-thumb.jpg", "videos/A-video.mp4", "media/videos/A.png",
                                                 "A.png", "random/A.png", "screenshots/A.webp", "covers/A.txt"]] ==
      [("box", "A"), ("box", "A"), ("video", "A"), None, None, None, ("screen", "A"), None])
check("norm: slashes, dots, case", G.norm("C:\\Games\\GBA\\.\\x\\..\\Zelda.GBA") == "c:/games/gba/zelda.gba"
      and G.norm("/a/./b/../c") == "/a/c", G.norm("C:\\Games\\GBA\\.\\x\\..\\Zelda.GBA"))
o = G.collect("/x/Other Game.gba", "Other", G.parse_gamelist(GAMELIST, "/x"), [])
check("bad rating and impossible year are ignored", o["rating"] == "" and o["year"] == "", o)

# ---- the Java core must agree, for the same gamelist and media list
rels = ["media/box2dfront/A.png", "images/A-thumb.jpg", "videos/A-video.mp4", "media/videos/A.png", "A.png", "random/A.png",
        "screenshots/A.webp", "covers/A.txt", "images/Metroid Fusion (USA)-image.png", "media/images/" + ZELDA + ".png",
        "media/titlescreens/" + ZELDA + ".png", "wheel/Legend of Zelda.png"]
CASES = [("/r/gba/" + ZELDA + ".gba", "The Legend of Zelda - The Minish Cap"), ("/r/gba/Other Game.gba", "Other"),
         ("/elsewhere/Metroid Fusion (USA).gba", "Metroid Fusion"), ("", "The Legend of Zelda - The Minish Cap"), ("", "Nobody")]
media = [("/r/gba/" + r, k[0], k[1]) for r in rels for k in [G.media_kind(r)] if k]
entries = G.parse_gamelist(GAMELIST, "/r/gba")
want = [json.dumps(G.media_kind(r)) for r in rels] + [G.norm("C:\\Games\\GBA\\.\\x\\..\\Zelda.GBA")]
for rom, name in CASES:
    want.append(json.dumps(G.collect(rom, name, entries, media, lambda p: "R:" + p), sort_keys=True, ensure_ascii=False))
root = os.path.join(os.path.dirname(os.path.dirname(HERE)), "android", "core", "src", "main", "java")
with tempfile.TemporaryDirectory() as out:
    lines = ["String gl = new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(a[0])), \"UTF-8\");",
             "java.util.List<String> rels = java.util.Arrays.asList(" + ",".join(json.dumps(r) for r in rels) + ");",
             "java.util.List<GameInfo.Media> media = new java.util.ArrayList<>();",
             "for (String r : rels) { String[] k = GameInfo.mediaKind(r); System.out.println(k == null ? \"null\" : "
             "\"[\\\"\" + k[0] + \"\\\", \\\"\" + k[1] + \"\\\"]\"); if (k != null) media.add(new GameInfo.Media(\"/r/gba/\" + r, k[0], k[1])); }",
             "System.out.println(GameInfo.norm(\"C:\\\\Games\\\\GBA\\\\.\\\\x\\\\..\\\\Zelda.GBA\"));",
             "java.util.List<java.util.Map<String, String>> e = GameInfo.parseGamelist(gl, \"/r/gba\");"]
    for rom, name in CASES:
        lines.append(f"System.out.println(Json.stringify(new java.util.TreeMap<>(GameInfo.collect({json.dumps(rom)}, {json.dumps(name)}, e, media, p -> \"R:\" + p))));")
    src = os.path.join(out, "InfoCheck.java")
    open(src, "w", encoding="utf-8").write("import net.netplay.core.*;\npublic class InfoCheck { public static void main(String[] a) throws Exception {\n"
        "System.setOut(new java.io.PrintStream(System.out, true, \"UTF-8\"));\n" + "\n".join(lines) + "\n} }\n")
    glf = os.path.join(out, "gamelist.xml"); open(glf, "w", encoding="utf-8").write(GAMELIST)
    subprocess.run(["javac", "-encoding", "UTF-8", "-d", out, "-sourcepath", root, src], check=True)
    got = subprocess.run(["java", "-cp", out, "InfoCheck", glf], capture_output=True, text=True, encoding="utf-8", check=True).stdout.splitlines()
    norm_json = lambda s: s if not s.startswith("{") else json.dumps(json.loads(s), sort_keys=True, ensure_ascii=False)
    diff = [(w, g) for w, g in zip(want, got) if norm_json(w) != norm_json(g)]
    check("Java and Python find the same art and details", not diff and len(got) == len(want), diff or (len(got), len(want)))

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
