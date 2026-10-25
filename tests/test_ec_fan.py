"""EC fan: the duty register is only checked in manual mode (the firmware
keeps its own value there in auto); a failed check hands the fan back."""
import sys
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import device, ec, fan

regs = {}
class FakeEc:
    name = "fake"
    def read_byte(self, r): return regs.get(r, 0)
    def write_byte(self, r, v): regs[r] = v
    def spec(self): return {}
ec._transport = FakeEc()
dev = device._next2("AYA", "AB09")
cfg = dev.ec_fan
c = fan.EcFanCtl(cfg, "fake")

# firmware mode with its own duty above pwm_max: fine
regs.update({cfg.mode_reg: cfg.auto, cfg.pwm_reg: 0x66})
c.probe()
c.set_percent(40)
assert regs[cfg.mode_reg] == cfg.manual and regs[cfg.pwm_reg] == 40
c.set_percent(70); assert regs[cfg.pwm_reg] == 70
c.set_auto(); assert regs[cfg.mode_reg] == cfg.auto

# manual mode with an impossible duty: refused, and the fan goes back to firmware
regs.update({cfg.mode_reg: cfg.manual, cfg.pwm_reg: 0x66})
try:
    c.probe(); raise SystemExit("probe should fail")
except ec.EcError:
    pass
ec.available = lambda *a: (True, "fake")
found, why = fan.find_backend(dev, True)
assert found is None and "firmware runs the fan" in why and regs[cfg.mode_reg] == cfg.auto
# an unknown mode value: nothing written
regs.update({cfg.mode_reg: 0x33})
assert fan.find_backend(dev, True)[0] is None and regs[cfg.mode_reg] == 0x33
print("all ec fan tests passed")
