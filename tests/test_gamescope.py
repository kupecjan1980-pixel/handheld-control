"""Game Mode HDR: confirm/auto-revert, re-apply per session, colorspace parsing."""
import os, sys, tempfile, shutil, types
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import controller, gamescope

clock = [1000.0]
controller.time = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda s: None,
                                        clock_gettime=lambda c: 0.0, CLOCK_BOOTTIME=0, CLOCK_MONOTONIC=1)
applied = []
class FakeGS:
    def __init__(self, pid, modes=("steam", "content", "off")):
        self.pid, self._modes, self.dead = pid, list(modes), False
    def alive(self): return not self.dead
    def modes(self): return self._modes
    def apply(self, mode): applied.append((self.pid, mode))
current = [FakeGS(100)]
gamescope.Gamescope.find = classmethod(lambda cls: current[0])
hdr = [True]
gamescope.hdr_output_active = lambda: hdr[0]

tmp = tempfile.mkdtemp()
ctl = controller.Controller(os.path.join(tmp, "s.json"))
for name in ("tick_fan",):
    setattr(ctl, name, lambda: None)
ctl.tick = lambda: ctl.tick_gamescope()

# 1. set + keep
ctl.set_gamescope_hdr("off"); assert applied[-1] == (100, "off") and ctl.gs_pending
st = ctl.state()["gamescope"]; assert st["pending"]["seconds"] == 20 and st["mode"] == "off" and st["saved"] == "steam"
ctl.confirm_gamescope_hdr(True); assert ctl.settings["gamescope"]["hdr"] == "off" and ctl.gs_pending is None
print("set + keep saves")
# 2. timeout reverts to previous saved mode
ctl.set_gamescope_hdr("content"); clock[0] += 21; ctl.tick()
assert applied[-1] == (100, "off") and ctl.settings["gamescope"]["hdr"] == "off" and "reverted" in ctl.errors["gamescope"]
print("unconfirmed change auto-reverts")
# 3. revert now
ctl.errors.clear(); ctl.set_gamescope_hdr("steam"); ctl.confirm_gamescope_hdr(False)
assert applied[-1] == (100, "off") and ctl.settings["gamescope"]["hdr"] == "off"
print("revert now restores previous")
# 4. new session: wait for Steam to settle, then apply saved mode once
current[0].dead = True; current[0] = FakeGS(200); n = len(applied)
ctl.tick(); assert len(applied) == n, "must not apply before settle"
clock[0] += 16; ctl.tick(); assert applied[-1] == (200, "off")
clock[0] += 1; ctl.tick(); assert len(applied) == n + 1, "applied once per session"
print("new session: applied once after settle")
# 5. off mode re-asserted if HDR comes back
hdr[0] = True; clock[0] += 11; ctl.tick(); assert applied[-1] == (200, "off") and len(applied) == n + 2
hdr[0] = False; clock[0] += 11; ctl.tick(); assert len(applied) == n + 2
print("off re-asserted when Steam turns HDR back on")
# 6. unsupported mode (old gamescope without hdr_content_driven)
current[0].dead = True; current[0] = FakeGS(300, modes=("steam", "off"))
ctl.errors.clear(); ctl.set_gamescope_hdr("content"); assert "doesn't support" in ctl.errors["gamescope"]
print("unsupported mode refused")

# colorspace parsing, using the eDP connector block from the real reports
real_glob = gamescope.glob.glob
# re-import module fresh to test the real parser
import importlib; gs2 = importlib.reload(gamescope)
for cs, want in (("BT2020_RGB", True), ("Default", False)):
    f = os.path.join(tmp, f"state-{cs}")
    open(f, "w").write(
        "crtc[151]: pipe A\n    enable=1\nconnector[274]: eDP-1\n    crtc=pipe A\n"
        f"    max_requested_bpc=12\n    colorspace={cs}\nconnector[283]: DP-1\n    colorspace=Default\n"
    )
    gs2.glob = types.SimpleNamespace(glob=lambda p, f=f: [f] if p.endswith("/state") else real_glob(p))
    assert gs2.hdr_output_active() is want, cs
print("colorspace parsing: BT2020_RGB -> on, Default -> off")
# convar scan on a fake gamescope binary
exe = os.path.join(tmp, "gamescope"); open(exe, "wb").write(b"\0hdr_enabled\0other\0")
g = gs2.Gamescope.__new__(gs2.Gamescope); g.exe = exe; g.ctl = "/usr/bin/gamescopectl"
g._convars = g._scan_convars(); assert g.modes() == ["steam", "off"]
open(exe, "wb").write(b"hdr_enabled\0hdr_content_driven"); g._convars = g._scan_convars()
assert g.modes() == ["steam", "content", "off"]
print("convar detection ok")
shutil.rmtree(tmp); print("all gamescope tests passed")
