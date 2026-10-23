"""AMD SMU TDP backend: family detection, mailbox protocol, limits in mW."""
import os, sys, struct, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import device, tdp

S = tdp.AmdSmu
cpu = lambda fam, model, vendor="AuthenticAMD": f"vendor_id\t: {vendor}\ncpu family\t: {fam}\nmodel\t\t: {model}\n"
assert S.supported(cpu(25, 80)), "Cezanne / Barcelo (7730U)"
assert S.supported(cpu(23, 96)) and S.supported(cpu(23, 104)), "Renoir, Lucienne"
assert not S.supported(cpu(25, 116)), "Phoenix: not listed"
assert not S.supported(cpu(25, 80, "GenuineIntel")) and not S.supported("")
print("family detection ok")


class FakeSmu(S):
    """Registers in a dict; writing the message register makes the SMU answer.
    Only register access is faked: S.send's protocol runs as is."""
    def __init__(self, answer=1, silent=False):
        super().__init__("/nonexistent")
        self.regs, self.msgs, self.answer, self.silent = {}, [], answer, silent

    def _open(self):
        return -1

    def _close(self, fd):
        pass

    def _rw(self, fd, addr, value=None):
        if value is None:
            return self.regs.get(addr, 0)
        self.regs[addr] = value
        if addr == self.MSG and not self.silent:
            assert self.regs[self.REP] == 0, "response cleared before the message"
            self.msgs.append((value, self.regs[self.ARGS]))
            if value == self.GET_VERSION:
                self.regs[self.ARGS] = 0x00412C00  # 65.44.0
            self.regs[self.REP] = self.answer
        return value


S.TIMEOUT = 0.05
smu = FakeSmu()
assert smu.version() == "65.44.0"
dev = device.detect()
smu.msgs.clear(); smu.apply(dev, 20, True)
assert smu.msgs == [(S.FAST, 25000), (S.SLOW, 22000), (S.STAPM, 20000)], smu.msgs
assert smu.current() == 20
smu.msgs.clear(); smu.apply(dev, 12, False)
assert smu.msgs == [(S.FAST, 12000), (S.SLOW, 12000), (S.STAPM, 12000)]
for bad, why in ((FakeSmu(answer=0xFE), "refused"), (FakeSmu(silent=True), "didn't answer")):
    try:
        bad.version(); raise AssertionError(why)
    except OSError as e:
        assert why in str(e), e
print("mailbox protocol, limit order and mW, errors ok")

# the real register access: index / data pair in a PCI config file
tmp = tempfile.mkdtemp(); cfg = f"{tmp}/config"
open(cfg, "wb").write(bytes(256))
real = S(cfg)
fd = os.open(cfg, os.O_RDWR)
real._rw(fd, 0x3B10528, 0x14)
assert struct.unpack("<I", os.pread(fd, 4, 0xB8))[0] == 0x3B10528, "address goes to 0xB8"
assert struct.unpack("<I", os.pread(fd, 4, 0xBC))[0] == 0x14, "value goes to 0xBC"
assert real._rw(fd, 0x3B10528) == 0x14
os.close(fd)
try:
    real.send(S.GET_VERSION); raise AssertionError("no SMU behind a plain file")
except OSError:
    pass
assert S.find(cpu(25, 116)) is None
print("pci index / data access and timeout ok")
print("all amd smu tests passed")
