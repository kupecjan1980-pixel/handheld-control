"""After an EC error the fan goes back to firmware and is taken up again
30 s later, at most FAN_RESTARTS times an hour; picking a mode retries now."""
import os, sys, tempfile, time
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import controller, device, ec, fan, watchdog

tmp = tempfile.mkdtemp()
controller.detect = lambda: device._next2("AYA", "AB09")
watchdog.restore = lambda spec: None


class FakeFan:
    name = "fake ec"
    fail = False

    def __init__(self): self.set = []
    def set_percent(self, p):
        if FakeFan.fail:
            raise ec.EcError("register 0x4A read 0x07, expected 0x00/0x01")
        self.set.append(p)
    def set_auto(self): self.set.append("auto")
    def restore_spec(self): return {}


made = []
fan.find_backend = lambda dev, allow_ec: (made.append(FakeFan()) or made[-1], None)
ctl = controller.Controller(os.path.join(tmp, "s.json"))
ctl._watchdog_start = ctl._watchdog_stop = lambda: None
fan.cpu_temp = lambda: 50
ctl.settings["fan"].update(mode="fixed", percent=60)
ctl.apply_fan()
assert made[-1].set == [60]

FakeFan.fail = True
ctl.fan_pct = None
ctl.tick_fan()
assert ctl.fan is None and "0x4A" in ctl.fan_why and ctl.fan_retry_at
assert ctl.state()["fan"]["restarting"]
FakeFan.fail = False
ctl.tick_fan_restart()
assert ctl.fan is None, "waits FAN_RETRY first"
ctl.fan_retry_at = time.monotonic() - 1
ctl.tick_fan_restart()
assert ctl.fan is made[-1] and made[-1].set == [60] and ctl.fan_retry_at is None
assert "restarted by itself" in ctl.state()["fan"]["note"]

# keeps failing: after FAN_RESTARTS restarts in an hour the firmware keeps it
for _ in range(ctl.FAN_RESTARTS + 2):
    FakeFan.fail = True
    ctl.fan_pct = None
    ctl.tick_fan()
    FakeFan.fail = False
    if ctl.fan_retry_at:
        ctl.fan_retry_at = time.monotonic() - 1
    ctl.tick_fan_restart()
assert ctl.fan is None and ctl.fan_retry_at is None and "times this hour" in ctl.errors["fan"]
# picking a mode takes it up again
ctl.update("fan", {"mode": "fixed", "percent": 70})
ctl.tick_fan_restart()
assert ctl.fan is made[-1] and made[-1].set[-1] == 70

# firmware mode when it failed: nothing to take up again
ctl.settings["fan"]["mode"] = "auto"
FakeFan.fail = True
ctl._guard("fan", lambda: made[-1].set_percent(1))
assert ctl.fan is None and ctl.fan_retry_at is None
print("all fan restart tests passed")
