"""Writes desktop/ui/icons.js: the Lucide icons (design/lucide/*.svg, ISC licence) as inner SVG markup by name.
  python3 design/make_ui_icons.py"""
import json, os, re
HERE = os.path.dirname(os.path.abspath(__file__))
out = {}
for f in sorted(os.listdir(os.path.join(HERE, "lucide"))):
    if f.endswith(".svg"):
        svg = open(os.path.join(HERE, "lucide", f), encoding="utf-8").read()
        body = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
        out[f[:-4]] = " ".join(re.sub(r"<!--.*?-->", "", body).split())
with open(os.path.join(HERE, "..", "desktop", "ui", "icons.js"), "w", encoding="utf-8") as f:
    f.write("// Lucide icons (https://lucide.dev, ISC licence, see design/lucide/LICENSE). Made by design/make_ui_icons.py.\n")
    f.write("export const ICONS = " + json.dumps(out, indent=0) + ";\n")
print(len(out), "icons")
