import sys, os, tempfile, shutil, json, subprocess
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import cpu, scx, sysfs
tmp = tempfile.mkdtemp()
w = lambda p, v: open(p, "w").write(f"{v}\n")
# SMT
cpu.SMT = f"{tmp}/smt"
assert "no SMT" in cpu.Smt().available()[1]
w(cpu.SMT, "notsupported"); assert "notsupported" in cpu.Smt().available()[1]
w(cpu.SMT, "on"); s = cpu.Smt(); assert s.available() == (True, None) and s.on() is True
assert s.set(False) and sysfs.read(cpu.SMT) == "off" and s.on() is False and not s.set(False)
print("smt ok")
# HWP dynamic boost / EPP boost
cpu.HWP_BOOST, cpu.NO_TURBO, cpu.EPP_BOOST = f"{tmp}/hwp", f"{tmp}/no_turbo", f"{tmp}/epp_boost"
assert "Intel only" in cpu.HwpBoost().available()[1]
w(cpu.NO_TURBO, 0); assert "HWP" in cpu.HwpBoost().available()[1]
w(cpu.HWP_BOOST, 0); h = cpu.HwpBoost(); assert h.available()[0] and h.on() is False
h.set(True); assert sysfs.read(cpu.HWP_BOOST) == "1"
assert "not in this kernel" in cpu.EppBoost().available()[1]
w(cpu.EPP_BOOST, "N"); e = cpu.EppBoost(); assert e.on() is False; e.set(True); assert e.on() is True
print("hwp / epp boost ok")
# CCX back on while SMT is off: sibling threads can't come online, that's fine
root = f"{tmp}/cpu"; cpu.CPU_ROOT = root
for c in range(4):
    os.makedirs(f"{root}/cpu{c}")
    w(f"{root}/cpu{c}/online", 1)
cpu.Ccx._detect = lambda self: [[0, 1], [2, 3]]
x = cpu.Ccx(f"{tmp}/cache.json")
x.set(True); assert x.on() is True
w(cpu.SMT, "off")
real_write = sysfs.write
def write(path, v):
    if path.endswith("cpu3/online") and str(v) == "1":
        raise PermissionError(1, "Operation not permitted")
    real_write(path, v)
sysfs.write = write
assert x.set(False) and sysfs.read(f"{root}/cpu2/online") == "1"
w(cpu.SMT, "on"); w(f"{root}/cpu2/online", 0)
try:
    x.set(False); raise AssertionError("error swallowed with SMT on")
except PermissionError:
    pass
sysfs.write = real_write
print("ccx with smt off ok")
# sched_ext via scx_loader (busctl faked)
scx.SCX = f"{tmp}/scx"; os.makedirs(f"{scx.SCX}/root")
calls = []
loader = {"SupportedSchedulers": ["scx_bpfland", "scx_lavd"], "SchedulerMode": 0}
def run(cmd, **kw):
    calls.append(cmd)
    if "get-property" in cmd:
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"type": "x", "data": loader[cmd[-1]]}).encode(), b"")
    if cmd[-1] == "StopScheduler":
        w(f"{scx.SCX}/state", "disabled")
    else:
        w(f"{scx.SCX}/state", "enabled"); w(f"{scx.SCX}/root/ops", cmd[-2].removeprefix("scx_") + "_1.0.16_x86_64")
        loader["SchedulerMode"] = int(cmd[-1])
    return subprocess.CompletedProcess(cmd, 0, b"", b"")
scx.proc.run = run
assert "no sched_ext" in scx.Scx().available()[1]
w(f"{scx.SCX}/state", "disabled"); k = scx.Scx()
assert k.available() == (True, None) and k.running() is None
k.apply("scx_lavd", "gaming")
assert calls[-1][-4:] == ["StartScheduler", "su", "scx_lavd", "1"] and k.running() == "lavd"
n = len(calls); k.apply("scx_lavd", "gaming"); assert not any("call" in c for c in calls[n:])
k.apply("scx_lavd", "powersave"); assert calls[-1][-4:] == ["SwitchScheduler", "su", "scx_lavd", "2"]
# exited scheduler isn't restarted every tick
w(f"{scx.SCX}/state", "disabled"); n = len(calls)
k.apply("scx_lavd", "powersave"); assert not any("call" in c for c in calls[n:])
k.tried.clear(); k.apply("scx_lavd", "powersave"); assert calls[-1][-4] == "StartScheduler"
k.apply("off", "gaming"); assert calls[-1][-1] == "StopScheduler" and k.running() is None
# busctl missing
def missing(cmd, **kw):
    raise FileNotFoundError("busctl")
scx.proc.run = missing
assert "scx_loader" in scx.Scx().available()[1]
print("scx ok")
shutil.rmtree(tmp)
print("all tweak tests passed")
