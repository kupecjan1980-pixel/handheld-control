"""CPU and GPU utilization for the header's Util column."""
import os, sys, tempfile, shutil, time
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import cpu, gpu

tmp = tempfile.mkdtemp()
w = lambda p, v: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w").write(f"{v}\n"))
stat = f"{tmp}/stat"; cpu.Util.STAT = stat
u = cpu.Util()
w(stat, "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 1 1 1"); assert u.sample() is None, "first sample has no delta"
w(stat, "cpu  150 0 150 900 0 0 0 0 0 0\ncpu0 1 1 1 1"); assert u.sample() == 50.0, u.value  # 100 busy of 200
w(stat, "cpu  150 0 150 900 100 0 0 0 0 0"); assert u.sample() == 0.0, "iowait counts as idle"
print("cpu util ok")

# xe: 100% minus the idle share of the time since the last tick
g = gpu.Gpu.__new__(gpu.Gpu)
g.dev, g.driver, g.hwmon = f"{tmp}/xe", "xe", None
g.pkg = g.core = g.gpu_meter = None
idle = f"{tmp}/xe/tile0/gt0/gtidle/idle_residency_ms"; w(idle, 1000)
g.idle_file, g.idle_prev, g.util = g._idle_file(), None, None
assert g.idle_file == idle
g.sample(); assert g._util() is None
g.idle_prev = (time.monotonic() - 2.0, 1000); w(idle, 1500)  # 0.5 s idle in 2 s
g.sample(); assert 70 <= g._util() <= 80, g._util()
# amdgpu: the driver's own number
w(f"{tmp}/amd/gpu_busy_percent", 37)
g.dev, g.driver = f"{tmp}/amd", "amdgpu"; assert g._util() == 37
print("gpu util (xe idle residency, amdgpu busy) ok")
shutil.rmtree(tmp); print("all util tests passed")
