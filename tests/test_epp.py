import sys, os, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import epp, sysfs
tmp = tempfile.mkdtemp()
cf = os.path.join(tmp, "cpufreq"); st = os.path.join(tmp, "status")
def mk(status, gov="performance", pref="performance", avail="performance"):
    shutil.rmtree(cf, ignore_errors=True)
    for i in (0, 1, 10):
        p = f"{cf}/policy{i}"; os.makedirs(p)
        for k, v in {"scaling_driver": "amd-pstate-epp", "scaling_governor": gov,
                     "energy_performance_preference": pref,
                     "energy_performance_available_preferences": avail,
                     "affected_cpus": str(i)}.items():
            open(f"{p}/{k}", "w").write(v + "\n")
    open(st, "w").write(status + "\n")
epp.CPUFREQ = cf
for k in epp.STATUS: epp.STATUS[k] = st
mk("active")
e = epp.Epp()
assert e.available() == (True, None)
assert e.options() == epp.STANDARD and [os.path.basename(p) for p in e.policies] == ["policy0", "policy1", "policy10"]
e.apply("balance_power")
for p in e.policies:
    assert sysfs.read(f"{p}/scaling_governor") == "powersave" and sysfs.read(f"{p}/energy_performance_preference") == "balance_power"
assert not e.drifted("balance_power")
open(f"{cf}/policy1/energy_performance_preference", "w").write("balance_performance\n")
assert e.drifted("balance_power")
e.restore()
for p in e.policies:
    assert sysfs.read(f"{p}/scaling_governor") == "performance" and sysfs.read(f"{p}/energy_performance_preference") == "performance"
print("active: apply/drift/restore ok")
mk("passive", gov="schedutil", pref="")
e = epp.Epp(); e.apply("power")
assert sysfs.read(st) == "active"
e.restore(); assert sysfs.read(st) == "passive"
print("passive -> active -> passive ok")
mk("active", gov="powersave", pref="balance_performance", avail="default performance balance_performance balance_power power")
assert epp.Epp().options() == epp.STANDARD
mk("active"); os.remove(st)
for k in epp.STATUS: epp.STATUS[k] = st
e = epp.Epp(); open(f"{cf}/policy0/scaling_driver", "w").write("acpi-cpufreq\n"); e = epp.Epp()
assert e.available()[0] is False
print("options / unsupported driver ok")
# offline CPU's policy (empty affected_cpus) is skipped, not written
mk("active"); open(f"{cf}/policy10/affected_cpus", "w").write("\n")
e = epp.Epp(); e.apply("power")
assert sysfs.read(f"{cf}/policy10/energy_performance_preference") == "performance"
assert sysfs.read(f"{cf}/policy0/energy_performance_preference") == "power" and not e.drifted("power")
print("offline policies skipped")
shutil.rmtree(tmp); print("all epp tests passed")
