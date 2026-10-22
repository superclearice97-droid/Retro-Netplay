"""The optional ScreenScraper download, against a pretend ScreenScraper (no network): what is sent (system, file name,
size, CRC32, the player's login; nothing else), how its answer is read (region and language picked, rating, date,
media), that only https pictures from screenscraper.fr are fetched, that everything lands in the app's own art
folder in Skraper's layout and the title card's finder reads it back, and that its errors come back in plain words.
NOTE: the real ScreenScraper has not been tried (it needs this app's developer ID)."""
import io, json, os, sys, tempfile, urllib.error, urllib.parse, zlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
home = tempfile.mkdtemp(); os.environ["HOME"] = home
import screenscraper as S
import gameinfo

fails = []
def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{str(info)[:1500]}]" if info and not ok else "")); ok or fails.append(name)

rom = os.path.join(home, "roms", "Sonic Battle (USA).gba")
os.makedirs(os.path.dirname(rom)); open(rom, "wb").write(b"\x01\x02\x03" * 1000)
game = {"name": "Sonic Battle", "system": "gba", "rom": rom}

try:
    S.lookup(game, "sam", "pw")
    check("without a developer ID it says it isn't available", False)
except S.ScrapeError as e:
    check("without a developer ID it says it isn't available", e.why == "nodev", e)
S.DEVID, S.DEVPASSWORD = "dev-id", "dev-secret"
try:
    S.lookup(game, "", "")
    check("without the player's login it asks for it", False)
except S.ScrapeError as e:
    check("without the player's login it asks for it", e.why == "login", e)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 100
JEU = {"id": "123",
       "noms": [{"region": "jp", "text": "Sonic Battle JP"}, {"region": "us", "text": "Sonic Battle"}],
       "synopsis": [{"langue": "fr", "text": "Texte"}, {"langue": "en", "text": "A test synopsis & more."}],
       "dates": [{"region": "jp", "text": "2003-12-04"}, {"region": "us", "text": "2004-01-05"}],
       "developpeur": {"id": "1", "text": "Test Dev"}, "editeur": {"text": "Test Pub"}, "joueurs": {"text": "1-4"},
       "note": {"text": "16"}, "genres": [{"noms": [{"langue": "fr", "text": "Combat"}, {"langue": "en", "text": "Fighting"}]}],
       "medias": [{"type": "box-2D", "region": "jp", "url": "https://neoclone.screenscraper.fr/box-jp", "format": "png"},
                  {"type": "box-2D", "region": "us", "url": "https://neoclone.screenscraper.fr/box-us", "format": "png"},
                  {"type": "ss", "region": "wor", "url": "https://evil.example.com/ss", "format": "png"},
                  {"type": "sstitle", "region": "wor", "url": "http://neoclone.screenscraper.fr/title", "format": "png"},
                  {"type": "fanart", "url": "https://screenscraper.fr.evil.com/fan", "format": "jpg"},
                  {"type": "wheel", "region": "wor", "url": "https://neoclone.screenscraper.fr/wheel", "format": "png"},
                  {"type": "video-normalized", "url": "https://neoclone.screenscraper.fr/vid", "format": "mp4"},
                  {"type": "ss", "region": "us", "url": "https://neoclone.screenscraper.fr/ss-us", "format": "png"}]}
seen = []

class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

def opener(req, timeout=30):
    url = req.full_url if hasattr(req, "full_url") else req
    seen.append(url)
    host = urllib.parse.urlparse(url).hostname
    assert host.endswith("screenscraper.fr"), url
    if "jeuInfos.php" in url or "jeuRecherche.php" in url:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if q.get("ssid") == ["quota"]:
            raise urllib.error.HTTPError(url, 430, "quota", {}, io.BytesIO(b"quota"))
        if "unknown" in q.get("romnom", [""])[0] or q.get("recherche") == ["Unknown Game"]:
            raise urllib.error.HTTPError(url, 404, "nf", {}, io.BytesIO("Erreur : Rom/Iso/Dossier non trouvée !".encode()))
        body = {"header": {}, "response": {"jeu": JEU}} if "jeuInfos" in url else {"response": {"jeux": [JEU]}}
        return Resp(json.dumps(body).encode())
    if url.endswith("/vid"):
        return Resp(b"\0\0\0\x18ftypmp42" + b"\0" * 50)
    if url.endswith("/wheel"):
        return Resp(b"<html>not a picture</html>")
    return Resp(PNG)

url = S.query(game, "sam", "pw")
q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
crc = "%08X" % (zlib.crc32(b"\x01\x02\x03" * 1000) & 0xFFFFFFFF)
check("a file is looked up by system, name, size and CRC32", url.startswith("https://api.screenscraper.fr/api2/jeuInfos.php?")
      and q["systemeid"] == ["12"] and q["romnom"] == ["Sonic Battle (USA).gba"] and q["romtaille"] == ["3000"] and q["crc"] == [crc], q)
check("only those, the app's IDs and the player's login are sent", set(q) == {"devid", "devpassword", "softname", "output", "ssid",
      "sspassword", "systemeid", "romtype", "romnom", "romtaille", "crc"}, sorted(q))
q2 = urllib.parse.parse_qs(urllib.parse.urlparse(S.query({"name": "Urban Reign", "system": "ps2", "rom": ""}, "sam", "pw")).query)
check("a streamed game with no file here is looked up by name", q2.get("recherche") == ["Urban Reign"] and q2["systemeid"] == ["58"]
      and "romnom" not in q2 and "crc" not in q2, q2)

fields, media = S.details(JEU)
check("US name, English synopsis, US date", fields["name"] == "Sonic Battle" and fields["desc"] == "A test synopsis & more."
      and fields["releasedate"] == "20040105T000000", fields)
check("rating out of 20 becomes 0-1; developer, publisher, players, English genre",
      (fields["rating"], fields["developer"], fields["publisher"], fields["players"], fields["genre"]) == ("0.80", "Test Dev", "Test Pub", "1-4", "Fighting"), fields)
urls = {k: u for k, _s, _t, u, _e in media}
check("box art: the US one", urls.get("box") == "https://neoclone.screenscraper.fr/box-us", urls)
check("only https from screenscraper.fr (not other sites, not look-alike names, not http)",
      urls.get("screen") == "https://neoclone.screenscraper.fr/ss-us" and "title" not in urls and "fanart" not in urls, urls)

root = os.path.join(home, ".netplay-matchmaker", "art")
saved = S.scrape(game, "sam", "pw", opener=opener, pause=0)
check("box, screenshot and clip saved; a page that isn't a picture is left out", sorted(saved) == ["box", "screen", "video"], saved)
check("the passwords are only ever sent to screenscraper.fr", all(urllib.parse.urlparse(u).hostname.endswith("screenscraper.fr") for u in seen))
info = gameinfo.Finder().find(game)
check("the title card's finder reads the download: details", info["name"] == "Sonic Battle" and info["year"] == "2004"
      and info["genre"] == "Fighting" and info["rating"] == "0.80", info)
check("the title card's finder reads the download: box, screenshot, clip", info["box"].endswith(os.path.join("box2dfront", "Sonic Battle (USA).png"))
      and info["screens"] and info["video"].endswith(".mp4"), info)
check("saved in the app's own folder, not the game folder", info["box"].startswith(root) and not os.path.exists(os.path.join(home, "roms", "media")))

S.scrape({"name": "Urban Reign", "system": "gba", "rom": ""}, "sam", "pw", opener=opener, pause=0, videos=False)
gl = open(os.path.join(root, "gba", "gamelist.xml"), encoding="utf-8").read()
check("a second game is added to the same gamelist, the first kept", gl.count("<game>") == 2, gl)
again = gameinfo.Finder().find(game)
check("the first game still reads back after the second", again["box"] == info["box"], again)
u = gameinfo.Finder().find({"name": "Urban Reign", "system": "gba", "rom": ""})
check("a streamed game's download reads back by name", u["box"].endswith(os.path.join("box2dfront", "urban-reign.png")) and u["video"] == "", u)

for g, user, why in [({"name": "x", "system": "gba", "rom": ""}, "quota", "quota"),
                     ({"name": "Unknown Game", "system": "gba", "rom": ""}, "sam", "not_found")]:
    try:
        S.scrape(g, user, "pw", opener=opener, pause=0)
        check(f"error {why} in plain words", False)
    except S.ScrapeError as e:
        check(f"error {why} in plain words, without the password", e.why == why and "pw" not in str(e) and "dev-secret" not in str(e), e)

# ---- the phone's core (ScreenScraper.java) must agree with this, for the same answers
import shutil, subprocess
ROOT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "android", "core", "src", "main", "java")
jd = tempfile.mkdtemp()
answers = {"https://neoclone.screenscraper.fr/box-us": "png", "https://neoclone.screenscraper.fr/ss-us": "png",
           "https://neoclone.screenscraper.fr/wheel": "html", "https://neoclone.screenscraper.fr/vid": "mp4"}
json.dump({"jeu": JEU, "answers": answers}, open(os.path.join(jd, "in.json"), "w"))
URLS = ["https://neoclone.screenscraper.fr/x", "https://screenscraper.fr/x", "http://neoclone.screenscraper.fr/x",
        "https://screenscraper.fr.evil.com/x", "https://evil.com/screenscraper.fr", "https://screenscraper.fr@evil.com/x", "nonsense"]
CODES = [404, 401, 423, 403, 426, 429, 430, 431, 500]
java = r"""
import net.netplay.core.*;
import java.util.*;
public class SsCheck {
  @SuppressWarnings("unchecked")
  public static void main(String[] a) throws Exception {
    System.setOut(new java.io.PrintStream(System.out, true, "UTF-8"));
    Map<String, Object> in = Json.parseObject(new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get(a[0])), "UTF-8"));
    Map<String, Object> jeu = (Map<String, Object>) in.get("jeu");
    Map<String, Object> ans = (Map<String, Object>) in.get("answers");
    System.out.println(Json.stringify(ScreenScraper.fields(jeu)));
    List<Object> media = new ArrayList<>();
    for (String[] m : ScreenScraper.media(jeu)) media.add(Arrays.asList((Object[]) m));
    System.out.println(Json.stringify(media));
    System.out.println(ScreenScraper.query("dev-id", "dev-secret", "gba", "Sonic Battle", "Sonic Battle (USA).gba", 3000, a[1], "sam", "pw"));
    System.out.println(ScreenScraper.query("dev-id", "dev-secret", "ps2", "Urban Reign", "", 0, "", "sam", "pw"));
    for (String u : new String[]{@URLS@}) System.out.println(ScreenScraper.ours(u));
    for (int c : new int[]{@CODES@}) System.out.println(ScreenScraper.why(c, "").why);
    ScreenScraper.Http http = (url, limit) -> {
      if (url.contains("jeuInfos.php") || url.contains("jeuRecherche.php")) return Json.stringify(Collections.singletonMap("response", Collections.singletonMap("jeu", jeu))).getBytes(java.nio.charset.StandardCharsets.UTF_8);
      String k = (String) ans.get(url);
      if (k == null) throw ScreenScraper.why(404, "");
      if (k.equals("png")) { byte[] b = new byte[108]; b[0] = (byte) 0x89; b[1] = 'P'; b[2] = 'N'; b[3] = 'G'; b[4] = 13; b[5] = 10; b[6] = 26; b[7] = 10; return b; }
      if (k.equals("mp4")) return "\0\0\0\u0018ftypmp42".getBytes(java.nio.charset.StandardCharsets.ISO_8859_1);
      return "<html>not a picture</html>".getBytes(java.nio.charset.StandardCharsets.UTF_8);
    };
    String url = ScreenScraper.query("dev-id", "dev-secret", "gba", "Sonic Battle", "Sonic Battle (USA).gba", 3000, "", "sam", "pw");
    System.out.println(Json.stringify(ScreenScraper.scrape(url, "gba", a[2], "Sonic Battle", new java.io.File(a[3]), http, true, 0)));
    System.out.println(Json.stringify(ScreenScraper.scrape(ScreenScraper.query("dev-id", "dev-secret", "gba", "Urban Reign", "", 0, "", "sam", "pw"),
        "gba", "", "Urban Reign", new java.io.File(a[3]), http, false, 0)));
    try { ScreenScraper.lookup(url, (u, l) -> { throw ScreenScraper.why(430, ""); }); } catch (ScreenScraper.ScrapeException e) { System.out.println(e.why); }
  }
}
""".replace("@URLS@", ",".join(json.dumps(u) for u in URLS)).replace("@CODES@", ",".join(map(str, CODES)))
open(os.path.join(jd, "SsCheck.java"), "w", encoding="utf-8").write(java)
subprocess.run(["javac", "-encoding", "UTF-8", "-d", jd, "-sourcepath", ROOT, os.path.join(jd, "SsCheck.java")], check=True)
jroot = os.path.join(jd, "art")
got = subprocess.run(["java", "-cp", jd, "SsCheck", os.path.join(jd, "in.json"), crc, rom, jroot], capture_output=True, text=True,
                     encoding="utf-8", check=True).stdout.splitlines()
pf, pm = S.details(JEU)
check("Java: the same details", json.loads(got[0]) == pf, (got[0], pf))
check("Java: the same media, in the same order", json.loads(got[1]) == [list(m) for m in pm], (got[1], pm))
check("Java: the same file lookup URL", got[2] == url, (got[2], url))
check("Java: the same name lookup URL", got[3] == S.query({"name": "Urban Reign", "system": "ps2", "rom": ""}, "sam", "pw"), got[3])
check("Java: the same rule for which addresses get the login", got[4:4 + len(URLS)] == [str(S._ours(u)).lower() for u in URLS], (got[4:11], URLS))
w = 4 + len(URLS)
check("Java: the same plain-words errors", got[w:w + len(CODES)] == [S._why(c, "")[0] for c in CODES], got[w:w + len(CODES)])
check("Java: box, screenshot and clip saved, the page that isn't a picture left out", sorted(json.loads(got[w + len(CODES)])) == ["box", "screen", "video"], got)
check("Java: a quota answer comes back as quota", got[-1] == "quota", got[-1])
jf = gameinfo.Finder(art=jroot)
ji, pi = jf.find(game), gameinfo.Finder().find(game)
same = {k: ji[k] for k in ("name", "desc", "year", "developer", "publisher", "genre", "players", "rating")} == \
       {k: pi[k] for k in ("name", "desc", "year", "developer", "publisher", "genre", "players", "rating")}
check("Java's saved gamelist reads back the same as Python's", same, (ji, pi))
check("Java's saved files read back: box, screenshot, clip", ji["box"].startswith(jroot) and ji["screens"] and ji["video"].startswith(jroot), ji)
ju = gameinfo.Finder(art=jroot).find({"name": "Urban Reign", "system": "gba", "rom": ""})
check("Java: a game with no file reads back by name, both games kept", ju["box"].endswith("urban-reign.png") and jf.find(game)["box"], ju)

print("ALL PASSED" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
