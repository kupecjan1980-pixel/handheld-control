"""ONEXPLAYER 3: Game Mode HDR switching is blocked; a saved mode is never re-applied."""
import json, os, sys, tempfile, shutil, types
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import controller, device, gamescope

clock = [1000.0]
controller.time = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda s: None,
                                        clock_gettime=lambda c: 0.0, CLOCK_BOOTTIME=0, CLOCK_MONOTONIC=1)
applied = []
class FakeGS:
    pid = 100
    def alive(self): return True
    def modes(self): return ["steam", "off"]
    def apply(self, mode): applied.append(mode)
gamescope.Gamescope.find = classmethod(lambda cls: FakeGS())
gamescope.hdr_output_active = lambda: True

tmp = tempfile.mkdtemp()
for dev in (device._oxp3("ONE-NETBOOK", "ONEXPLAYER 3"), device._next2("AYA", "AB09")):
    path = os.path.join(tmp, f"{dev.key}.json")
    json.dump({"gamescope": {"hdr": "off"}}, open(path, "w"))   # saved before the block existed
    controller.detect = lambda d=dev: d
    applied.clear()
    ctl = controller.Controller(path)
    ctl.tick_fan = lambda: None
    if dev.key == "oxp3":
        assert ctl.settings["gamescope"]["hdr"] == "steam"
        assert json.load(open(path))["gamescope"]["hdr"] == "steam", "reset must be saved"
        clock[0] += 60; ctl.tick_gamescope(); assert applied == [], applied
        ctl.set_gamescope_hdr("off"); assert applied == [] and "Blocked on this device" in ctl.errors["gamescope"]
        st = ctl.state()["gamescope"]; assert st["modes"] == [] and st["blocked"]
        print("oxp3: saved Off reset to Steam default, never applied, requests refused")
    else:
        assert ctl.settings["gamescope"]["hdr"] == "off"
        ctl.tick_gamescope(); assert applied == [], "waits for Steam to settle"
        clock[0] += 16; ctl.tick_gamescope(); assert applied == ["off"], applied
        assert ctl.state()["gamescope"]["modes"] == ["steam", "off"]
        print("next2: not blocked, saved mode re-applied")
shutil.rmtree(tmp); print("all hdr block tests passed")
