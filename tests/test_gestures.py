"""Edge swipe: one finger in from an edge, quick enough; the bottom edge is
learned from the first swipe; only that edge opens the keyboard."""
import os, sys, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import gestures as g

found = []
sw = g.Swipes((0, 1000), (0, 2000), found.append)
t = [0.0]

def touch(points, slot=0, tid=1, dt=0.05):
    sw.event(g.EV_ABS, g.ABS_MT_SLOT, slot, t[0])
    sw.event(g.EV_ABS, g.ABS_MT_TRACKING_ID, tid, t[0])
    for x, y in points:
        sw.event(g.EV_ABS, g.ABS_MT_POSITION_X, x, t[0]); sw.event(g.EV_ABS, g.ABS_MT_POSITION_Y, y, t[0])
        sw.event(g.EV_SYN, g.SYN_REPORT, 0, t[0]); t[0] += dt
    sw.event(g.EV_ABS, g.ABS_MT_TRACKING_ID, -1, t[0]); sw.event(g.EV_SYN, g.SYN_REPORT, 0, t[0])

touch([(500, 1990), (505, 1900), (510, 1700)])  # in from the high-y edge
assert found == ["y1"], found
touch([(10, 1000), (100, 1010), (250, 1000)])  # in from the low-x edge (portrait panel)
assert found[-1] == "x0"
touch([(500, 1000), (500, 600)])  # starts mid-screen: nothing
touch([(500, 1990), (500, 1950), (500, 1930)])  # too short
touch([(500, 1990)] + [(500, 1990 - i) for i in range(1, 30)] + [(500, 1500)])  # too slow (>0.8 s)
touch([(500, 1990), (900, 1800)])  # more sideways than in
assert len(found) == 2, found
# two fingers: no swipe
sw.event(g.EV_ABS, g.ABS_MT_SLOT, 1, 0); sw.event(g.EV_ABS, g.ABS_MT_TRACKING_ID, 7, 0)
sw.event(g.EV_ABS, g.ABS_MT_POSITION_X, 200, 0); sw.event(g.EV_ABS, g.ABS_MT_POSITION_Y, 500, 0)
touch([(500, 1990), (500, 1600)])
assert len(found) == 2, found

# learning: the first edge swipe sets the edge, later ones on it open the keyboard
saved, emitted = {}, []
e = g.EdgeSwipe(emitted.append, lambda: saved.get("edge"), lambda edge: saved.update(edge=edge))
e.calibrating = True
e.found("x1")
assert saved == {"edge": "x1"} and not e.calibrating and emitted == []
e._last = 0; e.found("y1"); assert emitted == []
e._last = 0; e.found("x1"); assert emitted == ["keyboard"]
e.found("x1"); assert emitted == ["keyboard"], "same swipe twice (real + virtual screen): once"

# the touchscreen: direct-touch property and MT positions (not a touchpad)
tmp = tempfile.mkdtemp()
g.SYS_INPUT = f"{tmp}/input"
def dev(n, props, abs_caps):
    d = f"{tmp}/input/{n}/device"; os.makedirs(d)
    open(f"{d}/properties", "w").write(props + "\n")
    os.makedirs(f"{d}/capabilities"); open(f"{d}/capabilities/abs", "w").write(abs_caps + "\n")
mt = hex((1 << 0x35) | (1 << 0x36) | (1 << 0x39) | (1 << 0x2F))[2:]
dev("event3", "5", mt)  # touchpad: POINTER + BUTTONPAD
dev("event7", "2", mt)  # touchscreen: DIRECT
dev("event9", "2", "3")  # direct but no MT positions
dev("event12", "2", mt)  # a virtual one (InputPlumber) as well
assert g.find_touchscreens() == ["/dev/input/event12", "/dev/input/event7"], g.find_touchscreens()
print("all gestures tests passed")
