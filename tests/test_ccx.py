"""First-CCX-only: L3 grouping, cached layout, offline/online, controller wiring."""
import json, os, sys, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import cpu, sysfs

tmp = tempfile.mkdtemp(); root = os.path.join(tmp, "cpu")
def build(groups):
    shutil.rmtree(root, ignore_errors=True)
    for g in groups:
        for c in cpu.parse_cpulist(g):
            d = f"{root}/cpu{c}/cache/index3"; os.makedirs(d)
            open(f"{d}/level", "w").write("3\n"); open(f"{d}/shared_cpu_list", "w").write(g + "\n")
            if c != 0:
                open(f"{root}/cpu{c}/online", "w").write("1\n")
cpu.CPU_ROOT = root
assert cpu.parse_cpulist("0-7,16-23") == list(range(8)) + list(range(16, 24))

# Strix Halo: 2 CCDs x 8 cores, SMT siblings +16
build(["0-7,16-23", "8-15,24-31"])
cache = os.path.join(tmp, "ccx.json")
c = cpu.Ccx(cache)
assert c.available() == (True, None) and c.keep_count() == 16 and c.total() == 32
assert c.others() == list(range(8, 16)) + list(range(24, 32)) and c.on() is False
assert c.set(True) and c.on() is True and sysfs.read(f"{root}/cpu8/online") == "0" and sysfs.read(f"{root}/cpu1/online") == "1"
assert not c.set(True), "idempotent"
# offline CPUs lose cache info: layout comes from the cache file
for x in c.others(): shutil.rmtree(f"{root}/cpu{x}/cache")
c2 = cpu.Ccx(cache); assert c2.groups == c.groups and c2.on() is True
c2.set(False); assert all(sysfs.read(f"{root}/cpu{x}/online") == "1" for x in c.others())
print("strix halo layout: 16 of 32 threads kept, cached layout used while offline")

os.remove(cache); c.set(True); for_x = [shutil.rmtree(f"{root}/cpu{x}/cache", ignore_errors=True) for x in c.others()]
assert "already offline" in cpu.Ccx(cache).available()[1]
build(["0-15"]); assert "single CCX" in cpu.Ccx(cache).available()[1]
print("unknown layout and single-CCX CPUs report why")

# controller: toggle, EPP re-applied when cores return, only unload brings them back
build(["0-7,16-23", "8-15,24-31"])
from hhctl import controller
ctl = controller.Controller(os.path.join(tmp, "s", "s.json"))
ctl.ccx = cpu.Ccx(cache)
epp_calls = []; ctl.apply_epp = lambda: epp_calls.append(1)
ctl.update("cpu", {"ccx_off": True}); assert ctl.ccx.on() is True and epp_calls == []
ctl.restore(); assert ctl.ccx.on() is True, "EC switch restore must not touch CPUs"
ctl.restore(unload=True); assert ctl.ccx.on() is False
ctl.update("cpu", {"ccx_off": True}); ctl.update("cpu", {"ccx_off": False})
assert ctl.ccx.on() is False and epp_calls == [1]
st = ctl.state()["cpu"]; assert st["ccx"]["available"] and st["ccx_keep"] == 16 and st["ccx_total"] == 32
json.dumps(st)
print("controller: toggle, EPP re-applied on return, unload restores, EC switch doesn't")
shutil.rmtree(tmp); print("all ccx tests passed")
