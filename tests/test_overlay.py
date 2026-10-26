import sys
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import overlay
killed = []
overlay.pids = lambda: [42]
overlay.os.kill = lambda pid, sig: killed.append(pid)
o = overlay.IdleOverlay()
o.tick(True, True, 0)
o.tick(True, False, 1)
o.tick(True, False, 5)
assert not killed, "not before 10 s without a game"
o.tick(True, False, 12)
assert killed == [42] and o.note and o.seen_idle
o2 = overlay.IdleOverlay(); killed.clear()
o2.tick(False, False, 0); o2.tick(False, False, 12)
assert not killed and o2.seen_idle, "off: only noted"
for t in (18, 24, 30, 36):
    o2.tick(True, False, t)
assert len(killed) == 3 and o2.gave_up, "Steam keeps restarting it: stop fighting"
o2.reset()
assert not o2.gave_up
print("all overlay tests passed")
