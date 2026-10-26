import sys
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import battery
b = battery.Battery.__new__(battery.Battery)
calls = []
b.limit_mode = lambda: "emulated"
b.set_behaviour = lambda v: calls.append(v)
cap = [85]
b.capacity = lambda: cap[0]
assert b.hold_for_sleep(85, "off") and calls == ["inhibit-charge"], "at the limit: paused for the sleep"
cap[0] = 83; calls.clear()
assert b.hold_for_sleep(85, "off") and calls, "just under it (within the hysteresis) too"
cap[0] = 60; calls.clear()
assert not b.hold_for_sleep(85, "off") and not calls, "well below: it charges on"
cap[0] = 90
assert not b.hold_for_sleep(100, "off") and not calls, "no limit set"
assert not b.hold_for_sleep(85, "always") and not calls, "bypass decides on its own"
b.limit_mode = lambda: "native"
assert not b.hold_for_sleep(85, "off") and not calls, "a firmware limit needs nothing"
print("all charge sleep tests passed")
