"""NEXT 2: charge register 0x1E reading 0x00 (never set) is accepted as auto."""
import os, sys, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import battery, device, ec

tmp = tempfile.mkdtemp(); io = os.path.join(tmp, "io")
def reset(val):
    b = bytearray(256); b[0x1E] = val; open(io, "wb").write(bytes(b))
def reg(): return open(io, "rb").read()[0x1E]
ec._transport = ec.EcSys(io)
ec.available = lambda *a: (True, "fake")
battery.find_battery = lambda: "/fake/BAT0"
dev = device._next2("AYA", "AB09")

reset(0x00)
b = battery.Battery(dev, True)
assert b.ec_ok, b.ec_why
b.set_bypass("always"); assert reg() == 0x55
b.set_bypass("off"); assert reg() == 0xAA
print("0x00 accepted; bypass writes 0x55, off writes 0xAA")

reset(0x12)
b = battery.Battery(dev, True)
assert not b.ec_ok and "0x1E reads 0x12" in b.ec_why and reg() == 0x12
print("other unexpected values are still refused, nothing written")
# status: 0 W while bypassed is shown as 0.0, not hidden
bat = os.path.join(tmp, "BAT0"); os.makedirs(bat)
def w(k, v): open(os.path.join(bat, k), "w").write(f"{v}\n")
for k, v in {"type": "Battery", "status": "Charging", "capacity": 85, "power_now": 0}.items(): w(k, v)
battery.find_battery = lambda: bat
battery.ac_online = lambda: True
reset(0x00); b = battery.Battery(dev, True); b.set_bypass("always")
st = b.status(); assert st["power_w"] == 0.0 and st["inhibited"] and st["status"] == "Charging", st
os.remove(os.path.join(bat, "power_now")); w("current_now", 0); w("voltage_now", 12_000_000)
assert b.status()["power_w"] == 0.0
w("current_now", 2_500_000); assert b.status()["power_w"] == 30.0
b.set_bypass("off"); assert not b.status()["inhibited"]
print("status: 0 W reported (power_now and current*voltage), inhibited flag set")
shutil.rmtree(tmp); print("all next2 charge tests passed")
