import sys
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import desktoppad as d
assert d.stick(0, -32768, 32767) == 0.0, "centre: nothing"
assert d.stick(3000, -32768, 32767) == 0.0, "inside the dead zone"
assert abs(d.stick(32767, -32768, 32767) - 1.0) < 1e-6 and abs(d.stick(-32768, -32768, 32767) + 1.0) < 1e-6
half = d.stick(16384, -32768, 32767)
assert 0.1 < half < 0.4, "a curve: half way is slow, for fine moves"
assert d.stick(5, 5, 5) == 0.0, "no range"
class P(d.Pad):
    def __init__(self):
        self.ranges = {c: (0, 255) if c in (d.ABS_Z, d.ABS_RZ, d.ABS_GAS, d.ABS_BRAKE) else (-32768, 32767)
                       for c in (d.ABS_X, d.ABS_Y, d.ABS_RX, d.ABS_RY, d.ABS_Z, d.ABS_RZ, d.ABS_GAS, d.ABS_BRAKE)}
        self.abs = {c: 0 for c in self.ranges}
p = P()
assert not p.trigger(d.ABS_RZ, d.ABS_GAS)
p.abs[d.ABS_GAS] = 200
assert p.trigger(d.ABS_RZ, d.ABS_GAS), "either trigger axis counts"
print("all desktoppad tests passed")
