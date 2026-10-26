import sys
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import autotune, fan
a = autotune.AutoTune()
def run(gpu, core, cap, lo=400, hi=5000):
    for i in range(11):
        a.feed(i * 2.0, gpu, core)
    return a.decide(21.0, cap, lo, hi)
assert run(98, 50, None) == 4800, "GPU-bound: the CPU gives way"
assert run(98, 50, 2800) == 2600 and "GPU-bound" in a.status
assert run(98, 50, 2600) == 2500, "stops at the floor"
assert run(98, 50, 2500) is False, "never below half the top clock"
assert run(60, 95, 2500) == 2900, "CPU-bound: back up"
assert run(60, 95, 4800) is None, "at the top: no limit"
assert run(30, 30, 3000) == 2800, "light load: save power"
assert run(85, 70, 3000) is False and "balanced" in a.status
a.feed(0.0, 99, 10)
assert a.decide(5.0, None, 400, 5000) is False, "not a full window yet"
# custom fan curve: never slows as it heats up
assert fan.custom_curve([30, 20, 50]) == [(40, 30), (50, 30), (60, 50), (70, 65), (80, 85), (88, 100)]
assert fan.custom_curve(None) == list(zip(fan.CUSTOM_TEMPS, fan.CUSTOM_DEFAULT))
print("all autotune tests passed")
