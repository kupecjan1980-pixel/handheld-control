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

# OXP light zone test: enable + color for zones 1..8, legend in the state
from hhctl import rgb
sent = []
class FakeOxp:
    def send(self, pkts): sent.extend(pkts)
rgb.OxpHidV1.find = classmethod(lambda cls: FakeOxp())
ctl.dev = __import__("hhctl.device", fromlist=["x"])._oxp3("ONE-NETBOOK", "ONEXPLAYER 3")
st = ctl.rgb_zone_test()
assert len(sent) == 16 and all(len(p) == 64 and p[0] == 0xB8 and p[1] == 0x3F and p[-1] == 0xB8 for p in sent)
assert [p[4] for p in sent[::2]] == list(range(1, 9)) and sent[1][3:7] == bytes([0xFE, 1, 2, 255])
assert st["rgb"]["zone_test"].startswith("zone 1: red") and st["rgb"]["zone_test_available"]
rgb.OxpHidV1.find = classmethod(lambda cls: None)
assert "Zone test" in ctl.rgb_zone_test()["errors"]["rgb"]
sent.clear()
o = rgb.OxpHidV1.__new__(rgb.OxpHidV1); o.send = lambda pkts: sent.extend(pkts)
o.apply("solid", 0, 100, 100, None)
assert [p[4] for p in sent] == [z for z in range(1, 9) for _ in (0, 1)], "zones 1-8, switch-on then color each"
assert [p[3] for p in sent[:2]] == [0xFD, 0xFE] and sent[1][6:9] == bytes([255, 0, 0])
sent.clear(); o.apply("off", 0, 100, 100, None)
assert len(sent) == 8 and all(p[3] == 0xFD and p[6] == 0 for p in sent), "off: switch-off for every zone, no colors"
print("oxp zone test ok")
shutil.rmtree(tmp); print("all rgb theme tests passed")
