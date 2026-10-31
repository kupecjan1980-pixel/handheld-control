"""Bundled fonts: manifest, @font-face CSS with inlined woff2 (TrueType for beat), licenses, missing file."""
import base64, os, sys, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import fonts

m = fonts.manifest()
assert set(m) == {"diablo", "matrix", "doom", "quake", "fraktur", "beat"}
families = set()
for name, font in m.items():
    css = fonts.css(name)
    assert css.count("@font-face") == len(font["faces"]) >= 1 and f"font-family: '{font['family']}'" in css
    assert "unicode-range: U+0000-" in css, f"{name}: basic Latin subset"
    for chunk, face in zip(css.split("base64,")[1:], font["faces"]):
        magic = b"\x00\x01\x00\x00" if face.get("format") == "truetype" else b"wOF2"
        assert base64.b64decode(chunk.split(")", 1)[0])[:4] == magic, f"{name}: inlined data is its format"
    assert os.path.exists(os.path.join(fonts.DIR, font["license"])), f"{name}: OFL license ships with the font"
    families.add(font["family"])
assert len(families) == len(m)
assert fonts.css("default") is None and fonts.css("nope") is None
print("fonts: diablo / matrix / doom / quake / fraktur / beat inlined, licenses present")

# the frontend's font list must match the manifest
src = open(os.path.join(sys.argv[1], "src", "index.tsx")).read()
for name, font in m.items():
    assert f'family: "{font["family"]}"' in src and f"  {name}: {{" in src, f"{name} missing in index.tsx"
print("frontend font list matches the manifest")

from hhctl.controller import Controller
tmp = tempfile.mkdtemp()
ctl = Controller(os.path.join(tmp, "s.json"))
assert ctl.settings["ui"]["font"] == "quake" and ctl.settings["ui"]["theme"] == "gold" and ctl.font_css("doom") == fonts.css("doom")
fonts.DIR = tmp; assert ctl.font_css("doom") is None, "missing files don't raise"
shutil.rmtree(tmp); print("all fonts tests passed")
