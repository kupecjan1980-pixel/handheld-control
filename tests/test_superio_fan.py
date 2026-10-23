import sys, os, tempfile, shutil, subprocess
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import fan, sysfs, watchdog
tmp = tempfile.mkdtemp(); fan.HWMON = tmp
w = lambda p, v: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w").write(f"{v}\n"))
mods = []
fan.proc.run = lambda cmd, **kw: mods.append(cmd[-1]) or subprocess.CompletedProcess(cmd, 0, b"", b"")
assert fan.superio_fan() == (None, None) and mods == ["nct6775", "nct6683", "it87"], "nothing found, drivers tried"
w(f"{tmp}/hwmon0/name", "k10temp")
# nct6683 on some ASRock boards: tachs, read-only pwm
w(f"{tmp}/hwmon1/name", "nct6686"); w(f"{tmp}/hwmon1/fan1_input", 1200); w(f"{tmp}/hwmon1/pwm1", 128)
os.chmod(f"{tmp}/hwmon1/pwm1", 0o444)
ctl, why = fan.superio_fan()
if os.geteuid() != 0:
    assert ctl is None and "can't control" in why, why
shutil.rmtree(f"{tmp}/hwmon1")
# nct6775: CPU fan on header 2 spinning, header 1 empty, BIOS SmartFan IV (5)
h = f"{tmp}/hwmon2"; w(f"{h}/name", "nct6796")
for n, rpm in ((1, 0), (2, 1500), (3, 0)):
    w(f"{h}/fan{n}_input", rpm); w(f"{h}/pwm{n}", 100); w(f"{h}/pwm{n}_enable", 5)
ctl, why = fan.superio_fan()
assert why is None and ctl.channels == (2,) and ctl.auto == 5, (ctl.channels, ctl.auto)
ctl.set_percent(50); assert sysfs.read(f"{h}/pwm2_enable") == "1" and sysfs.read(f"{h}/pwm2") == "128"
assert sysfs.read(f"{h}/pwm1_enable") == "5", "other headers untouched"
assert ctl.rpm() == 1500
ctl.set_auto(); assert sysfs.read(f"{h}/pwm2_enable") == "5"
# left in manual by a crash: fall back to the chip's automatic mode
w(f"{h}/pwm2_enable", 1); assert fan.superio_fan()[0].auto == 5
# all stopped (semi-passive): every header with a tach
for n in (1, 2, 3):
    w(f"{h}/fan{n}_input", 0)
w(f"{h}/pwm2_enable", 5)
assert fan.superio_fan()[0].channels == (1, 2, 3)
# watchdog puts every channel back
spec = fan.superio_fan()[0].restore_spec()
for n in (1, 2, 3):
    w(f"{h}/pwm{n}_enable", 1)
watchdog.restore(spec)
assert [sysfs.read(f"{h}/pwm{n}_enable") for n in (1, 2, 3)] == ["5", "5", "5"]
# old spec format (handhelds): pwm1_enable = 2
watchdog.restore({"kind": "hwmon", "path": h}); assert sysfs.read(f"{h}/pwm1_enable") == "2"
shutil.rmtree(tmp)
print("all superio fan tests passed")
