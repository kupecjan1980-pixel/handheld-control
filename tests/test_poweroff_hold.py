"""Power-off: logind's PrepareForShutdown holds the charge limit (once), records it, and the next start reports
whether the firmware charged while off."""
import json, os, sys, tempfile, types
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import sleep, controller, battery

# the watcher: PrepareForShutdown(true) calls before_shutdown, PrepareForSleep still calls before_sleep
got = []
w = sleep.SleepWatch(lambda: got.append("sleep"), None, lambda: got.append("off"))
w._release = lambda: got.append("release"); w._take = lambda: got.append("take")
w.handle(json.dumps({"member": "PrepareForShutdown", "payload": {"type": "b", "data": [True]}}))
assert got == ["off", "release"], got
got.clear(); w.handle(json.dumps({"member": "PrepareForShutdown", "payload": {"data": [False]}}))
assert got == ["take"], got
got.clear(); w.handle(json.dumps({"member": "PrepareForSleep", "payload": {"data": [True]}}))
assert got == ["sleep", "release"], got
w2 = sleep.SleepWatch(lambda: got.append("sleep"))  # no shutdown callback: the signal is ignored
got.clear(); w2._release = lambda: None
w2.handle(json.dumps({"member": "PrepareForShutdown", "payload": {"data": [True]}})); assert got == []
print("watcher: shutdown signal handled, sleep unchanged")

# the controller part, on a bare object
tmp = tempfile.mkdtemp()
c = controller.Controller.__new__(controller.Controller)
c.settings_path = os.path.join(tmp, "settings.json")
c.settings = {"battery": {"limit": 85, "bypass": "off"}, "sleep": {"rgb_off": False}, "rgb": {"enabled": False}}
c.rgb = None; c.off_done = False
cap = [86]; held = []
bat = types.SimpleNamespace(capacity=lambda: cap[0], limit_mode=lambda: "emulated", raw_behaviour=lambda: 0x55,
                            hold_for_sleep=lambda l, b: held.append(1) or cap[0] >= l - battery.HYSTERESIS)
c.bat = bat
battery.ac_online = lambda: True
c._before_shutdown(); c._before_shutdown()
assert held == [1], "held once even if the unload fallback runs too"
rec = json.load(open(os.path.join(tmp, controller.Controller.OFF_FILE)))
assert rec["cap"] == 86 and rec["held"] and rec["raw"] == 0x55, rec
cap[0] = 99
r = c._last_off_report()
assert "86 % → 99 %" in r and "paused (EC 0x55)" in r and "charged while off anyway" in r, r
assert c._last_off_report() is None, "reported once"
c.off_done = False; cap[0] = 86; c._before_shutdown(); cap[0] = 86
r = c._last_off_report(); assert "anyway" not in r, r
c.off_done = False; cap[0] = 60; c._before_shutdown(); cap[0] = 100
r = c._last_off_report(); assert "left on (below 82 %" in r and "anyway" not in r, r
print("power-off record and report ok")
print("all power-off tests passed")
