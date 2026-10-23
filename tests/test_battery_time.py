import sys, os, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import battery
tmp = tempfile.mkdtemp()
w = lambda n, v: open(f"{tmp}/{n}", "w").write(f"{v}\n")
b = battery.Battery.__new__(battery.Battery); b.bat = tmp; b.draw = None
w("status", "Charging"); assert b.remaining_min() is None
w("status", "Discharging"); w("energy_now", 40_000_000); w("power_now", 20_000_000)
assert b.remaining_min() == 120, "40 Wh at 20 W = 2:00"
w("power_now", 40_000_000); b.draw = (20_000_000, b.draw[1])
assert 100 < b.remaining_min() <= 120, "a spike only nudges the smoothed draw"
os.remove(f"{tmp}/energy_now"); os.remove(f"{tmp}/power_now"); b.draw = None
w("charge_now", 3_000_000); w("current_now", 2_000_000)
assert b.remaining_min() == 90, "3 Ah at 2 A = 1:30"
shutil.rmtree(tmp)
print("all battery time tests passed")
