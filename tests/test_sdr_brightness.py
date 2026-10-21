"""Game Mode brightness via gamescope's SDR-in-HDR nits property."""
import os, sys, tempfile, shutil, types
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import controller, gamescope

# --- encoding and xprop plumbing ------------------------------------------
assert gamescope.nits_to_cardinal(203.0) == 0x434B0000
assert gamescope.cardinal_to_nits(0x434B0000) == 203.0
g = gamescope.Gamescope.__new__(gamescope.Gamescope)
g._convars = {gamescope.SDR_ATOM}
runs = []
def fake_xprop(args, out=["GAMESCOPE_SDR_ON_HDR_CONTENT_BRIGHTNESS(CARDINAL) = 1128988672"]):
    runs.append(args); return types.SimpleNamespace(returncode=0, stdout=out[0], stderr="")
g._xprop = fake_xprop
assert g.sdr_brightness() == 203.0
fake_xprop.__defaults__[0][0] = "GAMESCOPE_SDR_ON_HDR_CONTENT_BRIGHTNESS:  not found."
assert g.sdr_brightness() is None
g.set_sdr_brightness(150); assert runs[-1] == ["-f", gamescope.SDR_ATOM, "32c", "-set", gamescope.SDR_ATOM, str(gamescope.nits_to_cardinal(150))]
g.set_sdr_brightness(None); assert runs[-1] == ["-remove", gamescope.SDR_ATOM]
print("float encoding + xprop args/parsing ok")

# --- controller trial / keep / revert / per-session ----------------------
clock = [1000.0]
controller.time = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda s: None,
                                        clock_gettime=lambda c: 0.0, CLOCK_BOOTTIME=0, CLOCK_MONOTONIC=1)
class FakeGS:
    def __init__(self, pid): self.pid, self.value, self.sets, self.dead = pid, None, [], False
    def alive(self): return not self.dead
    def modes(self): return ["steam", "off"]
    def apply(self, m): pass
    def sdr_brightness_supported(self): return True
    def sdr_brightness(self): return self.value
    def set_sdr_brightness(self, n): self.sets.append(n); self.value = n
cur = [FakeGS(100)]
gamescope.Gamescope.find = classmethod(lambda cls: cur[0])
gamescope.hdr_output_active = lambda: True
tmp = tempfile.mkdtemp()
ctl = controller.Controller(os.path.join(tmp, "s.json")); ctl.tick_fan = lambda: None
gs = cur[0]

st = ctl.update("sdr_brightness", {"enabled": True, "nits": 120})
assert gs.sets == [120] and st["sdr_brightness"]["pending"]["seconds"] == 20 and not st["sdr_brightness"]["confirmed"]
ctl.update("sdr_brightness", {"enabled": True, "nits": 100}); assert gs.sets[-1] == 100 and ctl.sb_pending["prev"] is None
ctl.confirm_sdr_brightness(True); assert ctl.settings["sdr_brightness"]["confirmed"] and ctl.sb_pending is None
ctl.update("sdr_brightness", {"nits": 80}); assert gs.sets[-1] == 80 and ctl.sb_pending is None
print("trial -> keep -> later changes apply directly")

ctl.update("sdr_brightness", {"nits": 10}); assert gs.sets[-1] == 40, "clamped to minimum"
ctl.update("sdr_brightness", {"enabled": False}); assert gs.sets[-1] is None
print("clamped to 40 nits; disabling removes the property")

# timeout revert on a fresh, unconfirmed setup
ctl.settings["sdr_brightness"].update(confirmed=False); gs.value = 300.0
ctl.update("sdr_brightness", {"enabled": True, "nits": 150}); assert gs.sets[-1] == 150
clock[0] += 21; ctl.tick_gamescope()
assert gs.sets[-1] == 300.0 and not ctl.settings["sdr_brightness"]["enabled"] and "reverted" in ctl.errors["sdr_brightness"]
print("unconfirmed trial reverts to the previous value after 20 s")

# confirmed: re-applied per session after settle, and after Steam resets it
ctl.settings["sdr_brightness"].update(enabled=True, confirmed=True, nits=90)
gs.dead = True; cur[0] = gs2 = FakeGS(200)
ctl.tick_gamescope(); assert gs2.sets == []
clock[0] += 16; ctl.tick_gamescope(); assert gs2.sets == [90]
gs2.value = None; clock[0] += 11; ctl.tick_gamescope(); assert gs2.sets == [90, 90]
clock[0] += 11; ctl.tick_gamescope(); assert gs2.sets == [90, 90]
print("re-applied per session after settle, and after Steam resets it")

# session ends during a trial -> trial dropped, toggle off
ctl.settings["sdr_brightness"].update(confirmed=False)
ctl.update("sdr_brightness", {"enabled": True, "nits": 110}); assert ctl.sb_pending
gs2.dead = True; cur[0] = FakeGS(300); ctl.tick_gamescope()
assert ctl.sb_pending is None and not ctl.settings["sdr_brightness"]["enabled"]
print("session restart during a trial drops it")
shutil.rmtree(tmp); print("all sdr brightness tests passed")
