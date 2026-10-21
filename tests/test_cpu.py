import sys, os, tempfile, shutil, json
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import cpu, sysfs
tmp = tempfile.mkdtemp()
cf = f"{tmp}/cpufreq"; os.makedirs(f"{cf}/policy0"); os.makedirs(f"{cf}/policy1")
cpu.CPUFREQ = cf; cpu.NO_TURBO = f"{tmp}/no_turbo"; cpu.ITMT = f"{tmp}/itmt"; cpu.AMD_PREFCORE = f"{tmp}/prefcore"
w = lambda p, v: open(p, "w").write(f"{v}\n")
# global boost
w(f"{cf}/boost", 1); b = cpu.Boost(); assert b.available()[0] and b.on() is True
b.set(False); assert sysfs.read(f"{cf}/boost") == "0" and b.on() is False
# per-policy boost
os.remove(f"{cf}/boost"); w(f"{cf}/policy0/boost", 1); w(f"{cf}/policy1/boost", 1)
b = cpu.Boost(); b.set(False); assert [sysfs.read(f"{cf}/policy{i}/boost") for i in (0, 1)] == ["0", "0"]
# intel no_turbo (inverted)
w(cpu.NO_TURBO, 0); b = cpu.Boost(); assert b.inverted and b.on() is True
b.set(False); assert sysfs.read(cpu.NO_TURBO) == "1" and b.on() is False
print("boost: global / per-policy / intel no_turbo ok")
# prefcore
p = cpu.Prefcore(); p._ensure_debugfs = lambda: None
w(f"{cf}/policy0/scaling_driver", "amd-pstate-epp"); w(cpu.AMD_PREFCORE, "disabled")
assert "amd_prefcore" in p.available()[1]
w(f"{cf}/policy0/scaling_driver", "intel_pstate"); os.remove(cpu.AMD_PREFCORE)
assert "hybrid" in p.available()[1]
w(cpu.ITMT, 1)
real = sysfs.selected
sysfs.selected = lambda path: "none" if "lockdown" in path else real(path)
assert p.available() == (True, None) and p.on() is True
p.set(False); assert sysfs.read(cpu.ITMT) == "0" and p.on() is False
sysfs.selected = lambda path: "integrity" if "lockdown" in path else real(path)
assert "lockdown" in p.available()[1]
sysfs.selected = real
print("prefcore: available / reasons / set ok")
shutil.rmtree(tmp)
# live + controller state on this machine
from hhctl import cpu as c2
import importlib; importlib.reload(c2)
print("live boost:", c2.Boost().paths[:1], c2.Boost().available(), c2.Boost().on())
print("live prefcore:", c2.Prefcore().available())
from hhctl.controller import Controller
d = tempfile.mkdtemp(); ctl = Controller(os.path.join(d, "s.json")); st = ctl.state(); json.dumps(st)
print("controller cpu state:", st["cpu"]); shutil.rmtree(d)
print("all cpu tests passed")
