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
shutil.rmtree(tmp); print("all next2 charge tests passed")
