"""GPU clock lock (xe, amdgpu) and CPU clock cap: fake sysfs, controller wiring."""
import json, os, sys, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import cpu, gpu, sysfs

tmp = tempfile.mkdtemp()
w = lambda p, v: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w").write(f"{v}\n"))
r = sysfs.read_int

# --- xe: Panther Lake-like render GT ---
dev = f"{tmp}/xe"; fq = f"{dev}/tile0/gt0/freq0"
for k, v in {"min": 400, "max": 2400, "rpn": 400, "rp0": 2400, "rpe": 1000, "act": 700}.items():
    w(f"{fq}/{k}_freq", v)
w(f"{fq}/throttle/status", 1); w(f"{fq}/throttle/reason_pl1", 1); w(f"{fq}/throttle/reason_thermal", 0)
c = gpu.GpuClock(dev, "xe")
assert c.available() == (True, None) and c.manual_ok() and c.hw_range() == (400, 2400)
assert c.orig == (400, 2400) and c.throttle() == ["pl1"]
c.apply("auto", None); assert not c.changed, "auto writes nothing until something was locked"
c.apply("max", None); assert (r(f"{fq}/min_freq"), r(f"{fq}/max_freq")) == (2400, 2400)
assert not c.drifted("max", None)
c.apply("manual", 1500); assert c.current() == (1500, 1500)
c.apply("manual", 9999); assert c.current() == (2400, 2400), "clamped to RP0"
c.apply("min", None); assert c.current() == (400, 400)
w(f"{fq}/max_freq", 2400); assert c.drifted("min", None)
c.apply("auto", None); assert c.current() == (400, 2400) and not c.changed
# min <= max must hold at every write: raising from a low lock writes max first
order = []; real_write = sysfs.write
sysfs.write = lambda p, v: (order.append(os.path.basename(p)), real_write(p, v))
c.apply("min", None); order.clear(); c.apply("max", None); assert order == ["max_freq", "min_freq"]
order.clear(); c.apply("min", None); assert order == ["min_freq", "max_freq"]
sysfs.write = real_write
# a lock left by a crash (min == max) isn't taken as the original
c2 = gpu.GpuClock(dev, "xe"); assert c2.orig == (400, 2400)
w(f"{fq}/throttle/status", 0); assert c.throttle() == []
print("xe: auto / min / max / manual, clamping, write order, drift, throttle reasons ok")

# --- amdgpu: Strix Halo-like APU ---
dev = f"{tmp}/amd"
w(f"{dev}/power_dpm_force_performance_level", "auto")
w(f"{dev}/pp_dpm_sclk", "0: 600Mhz\n1: 1000Mhz *\n2: 2900Mhz")
assert gpu.parse_dpm_levels(open(f"{dev}/pp_dpm_sclk").read()) == [600, 1000, 2900]
c = gpu.GpuClock(dev, "amdgpu")
assert c.available() == (True, None) and not c.manual_ok(), "no pp_od_clk_voltage: no manual"
assert c.hw_range() == (600, 2900) and c.current() == (None, None)
try:
    c.apply("manual", 1500); raise AssertionError("manual must fail without OD")
except OSError:
    pass
c.apply("max", None); assert sysfs.read(f"{dev}/power_dpm_force_performance_level") == "high"
c.apply("min", None); assert sysfs.read(f"{dev}/power_dpm_force_performance_level") == "low"
assert not c.drifted("min", None)
w(f"{dev}/power_dpm_force_performance_level", "auto"); assert c.drifted("min", None)
# manual through pp_od_clk_voltage; OD_RANGE wins over pp_dpm_sclk
od = f"{dev}/pp_od_clk_voltage"
w(od, "OD_SCLK:\n0: 600Mhz\n1: 2900Mhz\nOD_RANGE:\nSCLK:     800Mhz       2800Mhz")
assert c.manual_ok() and c.hw_range() == (800, 2800)
writes = []
sysfs.write = lambda p, v: (writes.append((os.path.basename(p), str(v))), real_write(p, v))
c.apply("manual", 1500)
assert writes == [("power_dpm_force_performance_level", "manual"), ("pp_od_clk_voltage", "s 0 1500"),
                  ("pp_od_clk_voltage", "s 1 1500"), ("pp_od_clk_voltage", "c")], writes
writes.clear(); w(od, "OD_RANGE:\nSCLK: 800Mhz 2800Mhz"); c.apply("auto", None)
assert writes == [("pp_od_clk_voltage", "r"), ("pp_od_clk_voltage", "c"),
                  ("power_dpm_force_performance_level", "auto")], writes
sysfs.write = real_write
assert not gpu.GpuClock(None, None).available()[0]
print("amdgpu: low / high, manual only with OD, s 0 / s 1 / c, reset to auto ok")

# --- CPU cap: hybrid Intel, P-cores 4800 MHz and E-cores 3500 MHz, one offline ---
cf = f"{tmp}/cpufreq"; cpu.CPUFREQ = cf
for i, (hi, online) in enumerate([(4800000, 1), (4800000, 1), (3500000, 1), (3500000, 0)]):
    p = f"{cf}/policy{i}"
    w(f"{p}/cpuinfo_min_freq", 400000); w(f"{p}/cpuinfo_max_freq", hi); w(f"{p}/scaling_max_freq", hi)
    w(f"{p}/scaling_cur_freq", 1000000); w(f"{p}/affected_cpus", i if online else "")
fc = cpu.FreqCap()
assert fc.available() == (True, None) and fc.range_mhz() == (400, 4800) and fc.limits_mhz() == [4800, 3500]
assert fc.avg_mhz() == 1000
fc.restore(); assert r(f"{cf}/policy0/scaling_max_freq") == 4800000, "restore is a no-op until applied"
fc.apply(4000)
assert [r(f"{cf}/policy{i}/scaling_max_freq") for i in range(4)] == [4000000, 4000000, 3500000, 3500000]
fc.apply(2500); assert fc.limits_mhz() == [2500]
assert r(f"{cf}/policy3/scaling_max_freq") == 3500000, "offline policy untouched"
fc.apply(100); assert fc.limits_mhz() == [400], "clamped to the hardware minimum"
fc.restore(); assert fc.limits_mhz() == [4800, 3500]
print("cpu cap: per-core-type clamp, offline policy skipped, restore ok")

# --- controller wiring ---
from hhctl import controller
ctl = controller.Controller(os.path.join(tmp, "s", "s.json"))
ctl.freqcap = cpu.FreqCap(); ctl.gpuclk = gpu.GpuClock(f"{tmp}/xe", "xe")
for k in ("boost", "prefcore", "ccx"):
    getattr(ctl, k).available = lambda: (False, "test")
assert ctl.settings["gpu"] == {"mode": "auto", "mhz": None} and ctl.settings["cpu"]["cap"] is False
ctl.update("gpu", {"mode": "manual", "mhz": 1800}); assert ctl.gpuclk.current() == (1800, 1800)
ctl.update("cpu", {"cap": True, "cap_mhz": 3000}); assert ctl.freqcap.limits_mhz() == [3000]
st = ctl.state(); json.dumps(st)
assert st["gpu"]["clock"]["modes"] == ["auto", "min", "max", "manual"] and st["gpu"]["clock"]["cur_max"] == 1800
assert st["cpu"]["cap"]["max"] == 4800 and st["cpu"]["cap"]["limits"] == [3000]
saved = json.load(open(ctl.settings_path)); assert saved["gpu"]["mhz"] == 1800 and saved["cpu"]["cap_mhz"] == 3000
# someone else rewrites the clocks: the 10 s check puts ours back
fq = f"{tmp}/xe/tile0/gt0/freq0"; w(f"{fq}/min_freq", 400); w(f"{fq}/max_freq", 2400)
w(f"{cf}/policy0/scaling_max_freq", 4800000)
ctl.last_epp_check = 0; ctl.tick(); assert ctl.gpuclk.current() == (1800, 1800) and ctl.freqcap.limits_mhz() == [3000]
ctl.restore(); assert ctl.gpuclk.current() == (1800, 1800), "EC switch restore leaves clocks"
ctl.restore(unload=True)
assert ctl.gpuclk.current() == (400, 2400) and ctl.freqcap.limits_mhz() == [4800, 3500]
ctl.apply_all(); assert ctl.gpuclk.current() == (1800, 1800), "re-applied on load / resume"
ctl.update("cpu", {"cap": False}); ctl.update("gpu", {"mode": "auto"})
assert ctl.freqcap.limits_mhz() == [4800, 3500] and ctl.gpuclk.current() == (400, 2400)
assert "gpu" not in ctl.errors and "cpu" not in ctl.errors, ctl.errors
print("controller: set, saved, drift re-applied, unload restores, EC switch doesn't")
shutil.rmtree(tmp); print("all clock tests passed")
