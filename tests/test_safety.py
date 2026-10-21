import sys, os, json, tempfile, time, shutil as _sh, threading
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import ec, fan, battery, device, watchdog, controller

tmp = tempfile.mkdtemp()
ecfile = os.path.join(tmp, "io")
def ec_reset(**regs):
    b = bytearray(256)
    for k, v in regs.items(): b[int(k[1:], 16)] = v
    open(ecfile, "wb").write(bytes(b))
def ec_get(reg): return open(ecfile, "rb").read()[reg]
ec.EC_IO = ecfile
watchdog.EC_IO = ecfile
ec.available = lambda *a: (True, "fake")
ec._transport = ec.EcSys(ecfile)
fan.ec.available = ec.available

# 1. guarded write
ec_reset(x4A=0x77)
try: ec.write(0x4A, 1, {0, 1}); raise SystemExit("should refuse")
except ec.EcError as e: print("refused unexpected value:", e)
assert ec_get(0x4A) == 0x77
ec_reset(x4A=0)
real = ec._write; ec._write = lambda r, v: None
try: ec.write(0x4A, 1, {0, 1}); raise SystemExit("should fail readback")
except ec.EcError as e: print("readback caught:", e)
ec._write = real

# 2. fan probe
dev = device._next2("AYA", "AB09")
dev.hwmon_names = ()
ec_reset(x4A=0x77)
f, why = fan.find_backend(dev, True); assert f is None and "0x4A" in why
f, why = fan.find_backend(dev, False); assert f is None and "Advanced" in why
ec_reset(x4A=0, x4B=30)
f, why = fan.find_backend(dev, True); assert f and why is None
f.set_percent(40); assert (ec_get(0x4A), ec_get(0x4B)) == (1, 40)
print("fan probe + EC write ok")

# 3. controller flow
run = os.path.join(tmp, "run"); os.makedirs(run)
watchdog.RUN_DIR = run; watchdog.STATE = run + "/state.json"; watchdog.HEARTBEAT = run + "/hb"; watchdog.PIDFILE = run + "/pid"
controller.detect = lambda: dev
controller.shutil = type("S", (), {"which": staticmethod(lambda n: None)})
battery.find_battery = lambda: "/fake/BAT0"
caps = {"v": 85}
battery.Battery.capacity = lambda self: caps["v"]
battery.Battery.status = lambda self: {}
ec_reset(x4A=0, x4B=30, x1E=0xAA)
ctl = controller.Controller(os.path.join(tmp, "s.json"))
assert ctl.fan is None and ctl.bat.limit_mode() is None, "EC must be off by default"
st = ctl.update("ec", {"enabled": True})
assert ctl.fan and ctl.bat.limit_mode() == "emulated", (ctl.fan, ctl.bat.limit_mode(), ctl.bat.ec_why)
ctl.update("fan", {"mode": "fixed", "percent": 60})
assert (ec_get(0x4A), ec_get(0x4B)) == (1, 60)
assert json.load(open(watchdog.STATE))["kind"] == "ec" and os.path.exists(watchdog.HEARTBEAT)
ctl.update("battery", {"limit": 80, "bypass": "off"})
ctl.tick(); assert ec_get(0x1E) == 0x55, hex(ec_get(0x1E))
caps["v"] = 76; ctl.tick(); assert ec_get(0x1E) == 0xAA
print("controller: EC switch, fixed fan, watchdog armed, emulated limit ok")
# EC goes weird -> fan disabled, not written
b = bytearray(open(ecfile, "rb").read()); b[0x4A] = 0x77; open(ecfile, "wb").write(bytes(b))
ctl.update("fan", {"percent": 70})
assert ctl.fan is None and "0x4A" in ctl.fan_why and ec_get(0x4B) == 60 and not os.path.exists(watchdog.STATE)
print("mismatch disables fan:", ctl.fan_why)
# switching EC off restores firmware control
ec_reset(x4A=0, x4B=30, x1E=0xAA)
ctl.update("ec", {"enabled": True}); ctl.update("fan", {"mode": "fixed", "percent": 50})
assert ec_get(0x4A) == 1
ctl.update("ec", {"enabled": False})
assert ec_get(0x4A) == 0 and ctl.fan is None and not os.path.exists(watchdog.STATE)
print("EC off -> fan back to auto, watchdog disarmed")

# 4. watchdog restores on stale heartbeat (hwmon + ec), exits when disarmed
watchdog.POLL = 0.05; watchdog.STALE = 0.3
hw = os.path.join(tmp, "hwmon"); os.makedirs(hw); open(hw + "/pwm1_enable", "w").write("1\n")
json.dump({"kind": "hwmon", "path": hw}, open(watchdog.STATE, "w"))
open(watchdog.HEARTBEAT, "w").write(str(time.monotonic()))
t0 = time.monotonic(); assert watchdog.main() == 0
assert open(hw + "/pwm1_enable").read().strip() == "2" and time.monotonic() - t0 >= 0.3
ec_reset(x4A=1)
json.dump({"kind": "ec", "mode_reg": 0x4A, "manual": 1, "auto": 0}, open(watchdog.STATE, "w"))
open(watchdog.HEARTBEAT, "w").write(str(time.monotonic() - 10))
assert watchdog.main() == 0 and ec_get(0x4A) == 0
# fresh heartbeat + disarm -> exits without touching
ec_reset(x4A=1)
json.dump({"kind": "ec", "mode_reg": 0x4A, "manual": 1, "auto": 0}, open(watchdog.STATE, "w"))
open(watchdog.HEARTBEAT, "w").write(str(time.monotonic() + 100))
threading.Timer(0.2, lambda: os.unlink(watchdog.STATE)).start()
assert watchdog.main() == 0 and ec_get(0x4A) == 1 and not os.path.exists(watchdog.PIDFILE)
print("watchdog: stale -> restore (hwmon, ec); disarm -> clean exit")
_sh.rmtree(tmp); print("all safety tests passed")
