"""Intel power: PL4 headroom on both RAPL zones, platform profile held while TDP is on."""
import json, os, sys, tempfile, shutil, subprocess
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import controller, device, proc, sysfs, tdp

tmp = tempfile.mkdtemp()
w = lambda p, v: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w").write(f"{v}\n"))
r = sysfs.read_int
dev = device._oxp3("ONE-NETBOOK", "ONEXPLAYER 3")

# --- RAPL: MSR zone with firmware PL4 40 W (as the OXP3 showed), MMIO zone with a huge one ---
zones = []
for z, pl4 in (("intel-rapl:0", 40), ("intel-rapl-mmio:0", 200)):
    d = f"{tmp}/{z}"; zones.append(d)
    w(f"{d}/name", "package-0"); w(f"{d}/enabled", 1)
    for i, (kind, v) in enumerate((("long_term", 15), ("short_term", 20), ("peak_power", pl4))):
        w(f"{d}/constraint_{i}_name", kind); w(f"{d}/constraint_{i}_power_limit_uw", v * 1_000_000)
rapl = tdp.Rapl(zones)
assert rapl.pl4_orig == {zones[0]: 40_000_000, zones[1]: 200_000_000}
rapl.apply(dev, 35, True)
lim = lambda z: [r(f"{z}/constraint_{i}_power_limit_uw") // 1_000_000 for i in range(3)]
assert lim(zones[0]) == [35, 37, 2 * dev.tdp_boost_max], lim(zones[0])
assert lim(zones[1]) == [35, 37, 200], "a higher firmware PL4 is kept"
rapl.apply(dev, 8, False)
assert lim(zones[0]) == [8, 8, 2 * dev.tdp_boost_max], "PL4 doesn't follow the TDP down"
print("rapl: PL1/PL2 follow TDP, PL4 held at max(firmware, 2x boost max) on both zones ok")

# --- platform profile, sysfs only (no daemon) ---
pp = f"{tmp}/platform_profile"; w(pp, "low-power"); w(pp + "_choices", "low-power balanced performance")
tdp.PlatformProfile._daemon = lambda self: None
prof = tdp.PlatformProfile(pp)
assert prof.orig == "low-power" and prof.available()
prof.restore(); assert sysfs.read(pp) == "low-power", "restore is a no-op until held"
assert prof.hold() and sysfs.read(pp) == "performance"
assert not prof.hold(), "already performance: nothing to do"
prof.restore(); assert sysfs.read(pp) == "low-power"
w(pp + "_choices", "low-power balanced"); assert not tdp.PlatformProfile(pp).hold()
w(pp + "_choices", "low-power balanced performance")
print("profile: hold / restore via sysfs, skipped without a performance choice ok")

# --- through powerprofilesctl when the daemon runs ---
calls = []
def fake_run(cmd, **kw):
    calls.append(cmd[1:])
    if cmd[1] == "set":
        w(pp, cmd[2])
    return subprocess.CompletedProcess(cmd, 0, "", "")
real_run = proc.run; proc.run = fake_run
tdp.PlatformProfile._daemon = lambda self: "/usr/bin/powerprofilesctl"
prof = tdp.PlatformProfile(pp)
assert prof.hold() and calls == [["set", "performance"]] and sysfs.read(pp) == "performance"
prof.restore(); assert calls[-1] == ["set", "low-power"]
proc.run = real_run; tdp.PlatformProfile._daemon = lambda self: None
print("profile: set through powerprofilesctl when it runs ok")

# --- controller wiring ---
ctl = controller.Controller(os.path.join(tmp, "s", "s.json"))
ctl.dev = dev; ctl.tdp = tdp.Rapl(zones); w(pp, "low-power"); ctl.profile = tdp.PlatformProfile(pp)
epp_applied = []; ctl.apply_epp = lambda: epp_applied.append(1)
ctl.update("tdp", {"enabled": True, "watts": 30})
assert sysfs.read(pp) == "performance" and lim(zones[0])[0] == 30 and epp_applied, "EPP re-applied after a profile switch"
st = ctl.state(); json.dumps(st); assert st["tdp"]["profile"] == "performance"
assert "profile" not in st["perf"]["limits"]["gpu"]
w(pp, "low-power")  # something else switches back
ctl.last_epp_check = 0; ctl.tick(); assert sysfs.read(pp) == "performance", "10 s check holds it"
ctl.restore(unload=True); assert sysfs.read(pp) == "low-power", "unload gives the original back"
ctl.apply_all(); assert sysfs.read(pp) == "performance"
ctl.update("tdp", {"enabled": False}); assert sysfs.read(pp) == "low-power", "TDP off restores"
st = ctl.state(); assert "profile" in st["perf"]["limits"]["gpu"] and "profile" in st["perf"]["limits"]["cpu"]
assert "tdp" not in ctl.errors, ctl.errors
print("controller: held with TDP on, re-held on tick, restored on unload / TDP off, shown as a limit")

# --- xe single throttle/reasons file (newer kernels) ---
from hhctl import gpu
xd = f"{tmp}/xe"; fq = f"{xd}/tile0/gt0/freq0"
for k, v in {"min": 400, "max": 2400, "rpn": 400, "rp0": 2400, "rpe": 1000}.items():
    w(f"{fq}/{k}_freq", v)
w(f"{fq}/throttle/reasons", "pl4"); c = gpu.GpuClock(xd, "xe"); assert c.throttle() == ["pl4"]
w(f"{fq}/throttle/reasons", "none"); assert c.throttle() == []
print("xe throttle/reasons file ok")
shutil.rmtree(tmp); print("all intel power tests passed")
