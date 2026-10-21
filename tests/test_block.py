"""ONEXPLAYER 3: PSR/DC display toggles can be turned off but never on."""
import os, sys, tempfile, time, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import controller, device

calls = []
class FakeKargs:
    name = "Limine"
    def configured(self): return []
    def in_entries(self, add, remove): return None
    def set(self, add, remove): calls.append((tuple(add), tuple(sorted(remove))))

tmp = tempfile.mkdtemp()
for dev in (device._oxp3("ONE-NETBOOK", "ONEXPLAYER 3"), device._next2("AYA", "AB09")):
    controller.detect = lambda d=dev: d
    ctl = controller.Controller(os.path.join(tmp, f"{dev.key}.json"))
    ctl.kargs = FakeKargs()
    ctl._display_params = lambda: {"psr": ["xe.enable_psr=0", "xe.enable_panel_replay=0"], "dc": ["xe.enable_dc=0"]}
    calls.clear()
    ctl.set_display("psr", True)
    while ctl.display_busy: time.sleep(0.01)
    if dev.key == "oxp3":
        assert calls == [] and "Blocked on this device" in ctl.errors["display"], (calls, ctl.errors)
        ctl.set_display("psr", False)
        while ctl.display_busy: time.sleep(0.01)
        assert calls == [((), ("xe.enable_panel_replay", "xe.enable_psr"))], calls
        assert ctl.state()["display"]["toggles"]["psr"]["blocked"]
        print("oxp3: turning on refused, turning off allowed")
    else:
        assert calls == [(("xe.enable_psr=0", "xe.enable_panel_replay=0"), ())], calls
        print("next2: not blocked")
shutil.rmtree(tmp); print("all block tests passed")
