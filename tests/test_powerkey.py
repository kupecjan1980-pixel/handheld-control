import sys, time
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import powerkey
calls = []
p = powerkey.PowerKey(lambda: calls.append(1))
p.press(0.2)
assert calls == [1], "a short press"
p.press(3.0)
assert calls == [1], "a hold is the firmware's"
real = powerkey._asleep_total
powerkey._asleep_total = lambda: real() + 60  # it slept a minute
p.press(0.2)
assert calls == [1], "the press that woke it"
p._resumed = time.monotonic() - 10
p.press(0.2)
assert calls == [1, 1], "later presses count again"
print("all powerkey tests passed")
