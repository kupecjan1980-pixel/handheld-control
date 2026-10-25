"""Controller shortcut: Xbox + B (either order) or View + Menu opens the
plugin once; gamepads are found by their buttons; PCs have it on by default."""
import os, sys, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import padchord as p

fired = []
c = p.Chord(p.CHORDS["guide_b"], lambda: fired.append(1))
c.key(p.BTN_MODE, 1); c.key(p.BTN_EAST, 1)
assert fired == [1]
c.key(p.BTN_EAST, 2); assert fired == [1], "repeat: nothing"
c.key(p.BTN_EAST, 0); c.key(p.BTN_MODE, 0)
c.key(p.BTN_EAST, 1); c.key(p.BTN_MODE, 1); assert fired == [1, 1], "either order"
c.key(p.BTN_EAST, 0); c.key(p.BTN_MODE, 0)
c.key(p.BTN_EAST, 1); c.key(p.BTN_EAST, 0); c.key(p.BTN_MODE, 1); assert fired == [1, 1], "not held together"
c.key(p.BTN_SOUTH, 1); assert fired == [1, 1]

# one press seen on two devices (real + virtual): opens once
emitted = []
pc = p.PadChord(emitted.append, lambda: "guide_b")
pc._fire(); pc._fire(); assert emitted == ["open_plugin"]

# gamepads: A, B, Xbox and Start buttons; keyboards and the like are left out
tmp = tempfile.mkdtemp()
p.SYS_INPUT = f"{tmp}/input"
def dev(n, codes):
    d = f"{tmp}/input/{n}/device/capabilities"; os.makedirs(d)
    bits = 0
    for code in codes:
        bits |= 1 << code
    words = []
    while bits:
        words.append(f"{bits & (2**64 - 1):x}"); bits >>= 64
    open(f"{d}/key", "w").write(" ".join(reversed(words)) + "\n")
dev("event3", [30, 31, 32])  # a keyboard
dev("event9", [p.BTN_SOUTH, p.BTN_EAST, p.BTN_MODE, p.BTN_START, p.BTN_SELECT])  # xpad
dev("event12", [p.BTN_SOUTH, p.BTN_EAST, p.BTN_START])  # no Xbox button
assert p.find_gamepads() == ["/dev/input/event9"], p.find_gamepads()

# default: on for PCs, off on the handhelds; "off" turns it off
from hhctl import controller, device
for dev_fn, want in ((lambda: device._next2("AYA", "AB09"), None), (None, "guide_b")):
    if dev_fn:
        controller.detect = dev_fn
    else:
        controller.detect = lambda: device.Device(key="generic", name="ASRock X600M-STX", cpu="amd",
                                                   tdp_min=10, tdp_max=88, tdp_default=65, tdp_boost_max=88)
    ctl = controller.Controller(os.path.join(tempfile.mkdtemp(), "s.json"))
    assert ctl.chord_setting() == want, (ctl.buttons.key, ctl.chord_setting())
ctl.settings["controller"]["chord"] = "off"; assert ctl.chord_setting() is None
ctl.settings["controller"]["chord"] = "select_start"; assert ctl.chord_setting() == "select_start"
print("all padchord tests passed")
