import sys, os, tempfile, shutil, subprocess
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import cpu, sysfs
tmp = tempfile.mkdtemp()
w = lambda p, v: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w").write(f"{v}\n"))
cpu.CPU_ROOT = f"{tmp}/cpu"
assert cpu.format_cpulist([0, 1, 2, 3, 8, 10, 11]) == "0-3,8,10-11" and cpu.format_cpulist([]) == ""
# Ryzen AI 9 370: 4 Zen 5 (8 threads, ~5.1 GHz, ranked) + 8 Zen 5c (16 threads, 3.3 GHz)
zen5 = list(range(0, 4)) + list(range(12, 16)); zen5c = list(range(4, 12)) + list(range(16, 24))
for c in zen5:
    w(f"{cpu.CPU_ROOT}/cpu{c}/cpufreq/cpuinfo_max_freq", 5100000 - 50000 * (c % 4))
for c in zen5c:
    w(f"{cpu.CPU_ROOT}/cpu{c}/cpufreq/cpuinfo_max_freq", 3300000)
real_read = sysfs.read
sysfs.read = lambda p, d=None: None if p.endswith("cpu_core/cpus") else real_read(p, d)
t = cpu.CoreTypes([zen5, zen5c])
assert t.fast == sorted(zen5) and t.small == sorted(zen5c)
# Strix Halo: two CCDs, same cores -> nothing to choose
assert "same type" in cpu.CoreTypes([zen5, zen5]).available()[1]
# a CCX offline: can't compare, not offered
os.remove(f"{cpu.CPU_ROOT}/cpu4/cpufreq/cpuinfo_max_freq")
z = [c for c in zen5c if c != 4]
for c in z:
    os.remove(f"{cpu.CPU_ROOT}/cpu{c}/cpufreq/cpuinfo_max_freq")
assert cpu.CoreTypes([zen5, zen5c]).small == []
# hybrid Intel: the P-core PMU decides
sysfs.read = lambda p, d=None: "0-3" if p.endswith("cpu_core/cpus") else real_read(p, d)
i = cpu.CoreTypes([])
assert i.fast == [0, 1, 2, 3] and i.small == sorted(set(range(24)) - {0, 1, 2, 3})
sysfs.read = real_read
# 370: core counts from SMT siblings (4 Zen 5 = 8 threads, 8 Zen 5c = 16 threads)
for c in range(24):
    core = c % 12
    w(f"{cpu.CPU_ROOT}/cpu{c}/topology/core_cpus_list", f"{core},{core + 12}")
assert cpu.CoreTypes.cores(zen5) == 4 and cpu.CoreTypes.cores(zen5c) == 8
print("core split ok")
# systemctl
allowed = {"v": ""}
calls = []
def run(cmd, **kw):
    calls.append(cmd)
    if cmd[1] == "show":
        return subprocess.CompletedProcess(cmd, 0, allowed["v"] + "\n", "")
    allowed["v"] = cmd[-1].split("=", 1)[1]
    return subprocess.CompletedProcess(cmd, 0, "", "")
cpu.proc.run = run
t.available = lambda: (True, None)
assert t.current() == "all"
t.apply("small"); assert calls[-1] == ["systemctl", "set-property", "--runtime", "user.slice", "AllowedCPUs=4-11,16-23"]
assert t.current() == "small"
n = len(calls); t.apply("small"); assert all(c[1] == "show" for c in calls[n:]), "no rewrite when already set"
t.apply("fast"); assert allowed["v"] == "0-3,12-15" and t.current() == "fast"
t.apply("all"); assert calls[-1][-1] == "AllowedCPUs=" and t.current() == "all"
allowed["v"] = "0-1"; assert t.current() == "custom"
shutil.rmtree(tmp)
print("all core type tests passed")
