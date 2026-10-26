import sys, os
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import thermal
os.path.exists = lambda p: True
thermal.glob.glob = lambda p: ["/sys/x/tcc_offset_degree_celsius"] if "tcc" in p else ["/dev/cpu/0/msr"]
regs = {thermal.TEMPERATURE_TARGET: 100 << 16, thermal.PLATFORM_INFO: 1 << 30}
thermal.IntelTcc._rd = staticmethod(lambda node, reg: regs[reg])
t = thermal.IntelTcc.find()
assert t and t.tjmax == 100 and t.path, "unlocked: the sysfs file is used"
regs[thermal.TEMPERATURE_TARGET] |= 1 << 31
assert thermal.IntelTcc.find() is None, "locked by the firmware: no temperature limit"
print("all tcc lock tests passed")
