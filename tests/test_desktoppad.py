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
        self.hat, self.down = [0, 0], set()
p = P()
assert not p.trigger(d.ABS_RZ, d.ABS_GAS)
p.abs[d.ABS_GAS] = 200
assert p.trigger(d.ABS_RZ, d.ABS_GAS), "either trigger axis counts"
p.hat = [-1, 1]
p.down = {d.BTN_SOUTH}
assert p.pressed() == {d.BTN_SOUTH, d.BTN_DPAD_LEFT, d.BTN_DPAD_DOWN}, "the hat D-pad counts as D-pad buttons"
assert d.KEYMAP[d.BTN_DPAD_UP] == (d.KEY_UP,) and d.KEYMAP[d.BTN_SOUTH] == (d.KEY_ENTER,) and d.KEYMAP[d.BTN_EAST] == (d.KEY_ESC,)
assert d.KEY_LEFTSHIFT in d.KEYS and d.KEY_TAB in d.KEYS
print("all desktoppad tests passed")
