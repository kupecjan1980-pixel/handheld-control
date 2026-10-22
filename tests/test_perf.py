"""Top readout: RAM, CPU watts (RAPL core or estimate), and CPU/GPU limit reasons."""
import json, os, sys, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import cpu, gpu, sysfs

tmp = tempfile.mkdtemp()
real_read = sysfs.read
sysfs.read = lambda p, d=None: "MemTotal: 32768000 kB\nMemFree: 1 kB\nMemAvailable: 24576000 kB\nHugePages_Total: 0\n" if p == "/proc/meminfo" else real_read(p, d)
assert gpu.memory_mb() == (8000, 32000)
sysfs.read = real_read
print("ram: used / total from meminfo ok")

# Intel MSR 0x64F: PL1 (bit 10) + thermal (bit 1) status, log bits (16+) ignored
msr = f"{tmp}/msr"
with open(msr, "wb") as f:
    f.seek(cpu.PERF_LIMIT_REASONS); f.write(((1 << 10) | (1 << 1) | (1 << 26)).to_bytes(8, "little"))
cpu.MSR_DEV = msr
lr = cpu.LimitReasons(); lr.intel = True
assert lr.read() == ["thermal", "pl1"]
lr.intel = False; assert lr.read() is None
cpu.MSR_DEV = f"{tmp}/none"; lr.intel = True; lr.tried_modprobe = True; assert lr.read() is None
print("msr 0x64F: status bits decoded, None on AMD / unreadable ok")

# CPU watts: estimated as package - GPU without a RAPL core zone
g = gpu.Gpu(); g.driver = "amdgpu"; g.dev = None; g.core = None; g.hwmon = None
class M: watts = 20.0
g.pkg = M(); g._gpu_watts = lambda: 12.0
st = g.state(); assert st["cpu_w"] == 8.0 and st["cpu_w_est"] is True
class C: watts = 6.04
g.core = C(); st = g.state(); assert st["cpu_w"] == 6.0 and st["cpu_w_est"] is False
hw = os.path.join(tmp, "hwmon"); os.makedirs(hw)
open(f"{hw}/temp1_input", "w").write("64500\n"); g.hwmon = hw
assert g.state()["temp_c"] == 64
g.hwmon = None; assert g.state()["temp_c"] is None, "integrated Intel: no GPU hwmon"
dev = os.path.join(tmp, "card"); os.makedirs(dev); g.dev = dev
for k, v in {"mem_info_vram_used": 3 << 30, "mem_info_vram_total": 16 << 30, "mem_info_gtt_used": 1 << 30}.items():
    open(f"{dev}/{k}", "w").write(f"{v}\n")
st = g.state(); assert (st["vram_mb"], st["vram_total_mb"]) == (3072, 16384)
g.dev = None
print("cpu watts: RAPL core, or package - GPU estimate ok; GPU temp from hwmon ok; VRAM used / total ok")

# controller reasons
from hhctl import controller, fan
ctl = controller.Controller(os.path.join(tmp, "s", "s.json"))
ctl.limit_reasons.read = lambda: None  # AMD: inferred
fan.cpu_temp = lambda: 70.0
ctl.boost.available = lambda: (True, None); ctl.boost.on = lambda: True
ctl.freqcap.available = lambda: (True, None); ctl.freqcap.peak_mhz = lambda: 2990
ctl.freqcap.range_mhz = lambda: (400, 5000)
ctl.gpuclk.available = lambda: (True, None); ctl.gpuclk.files = None
assert ctl._limits({"pkg_w": 19.0}, 20) == {"cpu": ["tdp"], "gpu": ["tdp"]}
assert ctl._limits({"pkg_w": 12.0}, 20) == {"cpu": [], "gpu": []}
fan.cpu_temp = lambda: 97.0
ctl.settings["cpu"].update(cap=True, cap_mhz=3000); ctl.boost.on = lambda: False
ctl.settings["gpu"]["mode"] = "manual"
assert ctl._limits({"pkg_w": 12.0}, 20) == {"cpu": ["thermal", "clock_limit", "boost_off"], "gpu": ["thermal", "mode"]}
# Intel: exact reasons from MSR and xe throttle files win over inference
fan.cpu_temp = lambda: 70.0; ctl.boost.on = lambda: True; ctl.settings["cpu"]["cap"] = False
ctl.settings["gpu"]["mode"] = "max"
ctl.limit_reasons.read = lambda: ["pl1"]
ctl.gpuclk.files = {"x": 1}; ctl.gpuclk.throttle = lambda: ["pl1"]
assert ctl._limits({"pkg_w": 5.0}, 20) == {"cpu": ["pl1"], "gpu": ["pl1"]}
ctl.gpuclk = gpu.GpuClock(None, None)
st = ctl.state(); json.dumps(st)
assert set(st["perf"]) >= {"cpu_mhz", "cpu_temp_c", "ram_used_mb", "ram_total_mb", "tdp_w", "limits"}
assert st["perf"]["cpu_temp_c"] == 70 and "temp_c" in st["gpu"]
shutil.rmtree(tmp); print("all perf tests passed")
