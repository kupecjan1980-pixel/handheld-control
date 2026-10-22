"""RGB "Match theme color": the lights take the theme's hue, and follow theme changes."""
import os, sys, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import controller

tmp = tempfile.mkdtemp()
ctl = controller.Controller(os.path.join(tmp, "s", "s.json"))
calls = []
class FakeRgb:
    name = "fake"; modes = ["solid", "pulse", "off"]; effects = []
    def apply(self, mode, hue, sat, bright, effect):
        calls.append((mode, hue, sat, bright))
ctl.rgb = FakeRgb()
assert ctl.settings["rgb"]["match_theme"] is False
ctl.update("rgb", {"enabled": True, "mode": "solid", "hue": 300, "sat": 50, "bright": 80})
assert calls[-1] == ("solid", 300, 50, 80), "own color while not matching"
ctl.update("ui", {"theme": "red"}); assert calls[-1] == ("solid", 300, 50, 80), "theme alone doesn't touch the lights"
n = len(calls)
ctl.update("rgb", {"match_theme": True}); assert calls[-1] == ("solid", 0, 100, 80)
ctl.update("ui", {"theme": "gold"}); assert calls[-1] == ("solid", 42, 100, 80), "follows a theme change"
ctl.update("ui", {"theme": "default"}); assert calls[-1] == ("solid", 300, 50, 80), "Default theme: own color"
ctl.update("ui", {"font": "doom", "theme": "green"}); assert calls[-1] == ("solid", 130, 100, 80)
ctl.update("rgb", {"match_theme": False}); assert calls[-1] == ("solid", 300, 50, 80)
ctl.update("rgb", {"enabled": False}); k = len(calls); ctl.update("ui", {"theme": "blue"}); assert len(calls) == k
assert "rgb" not in ctl.errors
shutil.rmtree(tmp); print("all rgb theme tests passed")
