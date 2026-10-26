import sys
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import autotune, fan
a = autotune.AutoTune()
CAN = {"lo": 400, "hi": 5000, "boost": True, "epp": True, "tdp": 20, "tdp_min": 5}
def run(gpu, core, cpu=None, tdp=None, epp=None, can=CAN):
    for i in range(11):
        a.feed(i * 2.0, gpu, core)
    s = {"cpu": {"cap": False, "cap_mhz": None, "boost": None, **(cpu or {})},
         "tdp": {"enabled": False, "watts": None, **(tdp or {})},
         "epp": {"enabled": False, "value": "balance_performance", **(epp or {})}}
    return a.decide(21.0, s, can)
floor = {"cap": True, "cap_mhz": 2500}
# light load: clock, then boost, then EPP, then TDP; never SMT or cores
assert run(30, 30) == {"cpu": {"cap": True, "cap_mhz": 4800}}
assert run(30, 30, floor) == {"cpu": {"boost": False}}
assert run(30, 30, {**floor, "boost": False}) == {"epp": {"enabled": True, "value": "balance_power"}}
low = dict(cpu={**floor, "boost": False}, epp={"enabled": True, "value": "balance_power"})
assert run(30, 30, **low) == {"tdp": {"enabled": True, "watts": 19}}
assert run(30, 30, tdp={"enabled": True, "watts": 12}, **low) is None and "as low" in a.status, "TDP floor: 60 % of 20 W"
# GPU-bound: power back to the GPU first
assert run(98, 50, tdp={"enabled": True, "watts": 15}, **low) == {"tdp": {"enabled": True, "watts": 17}}
assert run(98, 50) == {"cpu": {"cap": True, "cap_mhz": 4800}}
assert run(98, 50, floor) == {"cpu": {"boost": False}}
# CPU-bound: undo in reverse
assert run(60, 95, tdp={"enabled": True, "watts": 15}, **low) == {"tdp": {"enabled": True, "watts": 17}}
assert run(60, 95, **low) == {"epp": {"value": "balance_performance"}}
assert run(60, 95, {**floor, "boost": False}) == {"cpu": {"boost": True}}
assert run(60, 95, floor) == {"cpu": {"cap": True, "cap_mhz": 2900}}
assert run(60, 95, {"cap": True, "cap_mhz": 4800}) == {"cpu": {"cap": False}}
assert run(60, 95) is None and "full speed" in a.status
assert run(85, 70) is None and "balanced" in a.status
# a device with only some levers
assert run(30, 30, can={"boost": False, "epp": False, "tdp": None}) is None
assert run(30, 30, can={"tdp": 15, "tdp_min": 4}) == {"tdp": {"enabled": True, "watts": 14}}
a.feed(0.0, 99, 10)
assert a.decide(5.0, {"cpu": {}, "tdp": {}, "epp": {}}, CAN) is None, "not a full window yet"
assert fan.custom_curve([30, 20, 50]) == [(40, 30), (50, 30), (60, 50), (70, 65), (80, 85), (88, 100)]
assert fan.custom_curve(None) == list(zip(fan.CUSTOM_TEMPS, fan.CUSTOM_DEFAULT))
print("all autotune tests passed")
