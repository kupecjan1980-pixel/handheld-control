"""Bundled fonts: @font-face CSS with inlined woff2, settings default, missing file."""
import base64, os, sys, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import fonts

css = fonts.css("diablo")
assert css.count("@font-face") == 2 and "font-family: 'HHCinzel'" in css and "unicode-range: U+0000-00FF" in css
for chunk in css.split("base64,")[1:]:
    assert base64.b64decode(chunk.split(")", 1)[0])[:4] == b"wOF2", "inlined data is woff2"
assert fonts.css("default") is None and fonts.css("nope") is None
assert os.path.exists(os.path.join(fonts.DIR, "OFL.txt")), "OFL license ships with the font"
print("diablo font: two subsets inlined as woff2, license present")

from hhctl.controller import Controller
tmp = tempfile.mkdtemp()
ctl = Controller(os.path.join(tmp, "s.json"))
assert ctl.settings["ui"]["font"] == "default" and ctl.font_css("diablo") == css
fonts.DIR = tmp; assert ctl.font_css("diablo") is None, "missing file doesn't raise"
shutil.rmtree(tmp); print("all fonts tests passed")
