import sys
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import autotune, fan
a = autotune.AutoTune()
def run(gpu, core, cpu, smt=True, small=True, lo=400, hi=5000):
    for i in range(11):
        a.feed(i * 2.0, gpu, core)
    return a.decide(21.0, cpu, lo, hi, smt, small)
free = {"cap": False, "cap_mhz": None, "smt": None, "cores": None}
assert run(98, 50, free) == {"cap": True, "cap_mhz": 4800}, "GPU-bound: the clock gives way first"
at_floor = {**free, "cap": True, "cap_mhz": 2500}
assert run(98, 50, at_floor) == {"smt": False}, "then SMT"
assert run(98, 50, {**at_floor, "smt": False}) is None and "as low" in a.status, "GPU-bound never goes to small cores"
assert run(30, 30, {**at_floor, "smt": False}) == {"cores": "small"}, "light load: small cores only"
assert run(30, 30, at_floor, smt=False) == {"cores": "small"}, "no SMT on this CPU: straight to the small cores"
assert run(30, 30, at_floor, smt=False, small=False) is None
low = {**at_floor, "smt": False, "cores": "small"}
assert run(60, 95, low) == {"cores": "all"}, "CPU-bound: all cores back first"
assert run(60, 95, {**low, "cores": "all"}) == {"smt": True}, "then SMT"
assert run(60, 95, {**at_floor, "smt": True}) == {"cap": True, "cap_mhz": 2900}, "then the clock"
assert run(60, 95, {**free, "cap": True, "cap_mhz": 4800}) == {"cap": False}, "at the top: no limit"
assert run(85, 70, at_floor) is None and "balanced" in a.status
a.feed(0.0, 99, 10)
assert a.decide(5.0, free, 400, 5000, True, True) is None, "not a full window yet"
# custom fan curve: never slows as it heats up
assert fan.custom_curve([30, 20, 50]) == [(40, 30), (50, 30), (60, 50), (70, 65), (80, 85), (88, 100)]
assert fan.custom_curve(None) == list(zip(fan.CUSTOM_TEMPS, fan.CUSTOM_DEFAULT))
print("all autotune tests passed")
