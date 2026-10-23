"""AMD SMU TDP backend: family detection, mailbox protocol, limits in mW."""
import os, sys, struct, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import device, tdp

S = tdp.AmdSmu
cpu = lambda fam, model, vendor="AuthenticAMD": f"vendor_id\t: {vendor}\ncpu family\t: {fam}\nmodel\t\t: {model}\n"
fam = lambda f, m: (S.detect(cpu(f, m)) or tdp.SmuFamily("none", 0, (), 0, (), ())).name
assert fam(25, 80) == "Cezanne / Barcelo", "7730U, 5700G"
assert fam(23, 96) == fam(23, 104) == "Renoir / Lucienne"
assert fam(23, 17) == fam(23, 24) == fam(23, 32) == "Raven / Picasso / Dali", "Athlon 3000G, 2400G, 3400G"
assert fam(25, 116) == fam(25, 117) == fam(25, 120) == "Phoenix / Hawk Point", "8700G, 8840U"
assert S.detect(cpu(25, 116)).rep == 0x3B10578 and S.detect(cpu(25, 80)).rep == 0x3B10564
assert S.detect(cpu(23, 17)).mp1 == (0x1A, 0x1B, 0x1C) and S.detect(cpu(23, 17)).rsmu == (0x2E, 0x2F, 0x30)
assert fam(26, 36) == fam(26, 32) == fam(26, 96) == "Strix Point / Krackan Point", "AI 9 370, AI 7 350"
assert S(S.detect(cpu(26, 36))).mp1 == (0x3B10928, 0x3B10978, 0x3B10998)
assert S(S.detect(cpu(25, 80))).mp1 == (0x3B10528, 0x3B10564, 0x3B10998)
assert not S.supported(cpu(25, 33)), "Vermeer: no APU"
assert not S.supported(cpu(25, 80, "GenuineIntel")) and not S.supported("")
print("family detection ok")


CEZ = S.detect(cpu(25, 80))


class FakeSmu(S):
    """Registers in a dict; writing a message register makes that mailbox
    answer. Only register access is faked: S.send's protocol runs as is."""
    def __init__(self, answer=1, silent=False, family=CEZ, rsmu="same"):
        super().__init__(family, "/nonexistent")
        self.regs, self.msgs, self.answer, self.quiet = {}, [], answer, silent
        self.rsmu_answer = answer if rsmu == "same" else rsmu  # None: never answers
        self.MSG, self.REP, self.ARGS = self.mp1

    def _open(self):
        return -1

    def _close(self, fd):
        pass

    def _rw(self, fd, addr, value=None):
        if value is None:
            return self.regs.get(addr, 0)
        self.regs[addr] = value
        for (msg, rep, args), answer, box in ((self.mp1, self.answer, "mp1"), (S.RSMU, self.rsmu_answer, "rsmu")):
            if addr == msg and answer is not None and not self.quiet:
                assert self.regs[rep] == 0, "response cleared before the message"
                self.msgs.append((box, value, self.regs[args]))
                if value == self.GET_VERSION:
                    self.regs[args] = 0x00412C00  # 65.44.0
                self.regs[rep] = answer
        return value


S.TIMEOUT = 0.05
smu = FakeSmu()
assert smu.version() == "65.44.0"
dev = device.detect()
smu.msgs.clear(); smu.apply(dev, 20, True)
assert smu.msgs == [
    ("mp1", 0x15, 25000), ("rsmu", 0x32, 25000), ("mp1", 0x16, 22000), ("rsmu", 0x33, 22000),
    ("mp1", 0x14, 20000), ("rsmu", 0x31, 20000),
], smu.msgs
assert smu.current() == 20
# desktop APU refusing MP1: RSMU alone is enough
d = FakeSmu(answer=0xFE, rsmu=1); d.apply(dev, 12, False)
assert [m for m in d.msgs if m[0] == "rsmu"] == [("rsmu", 0x32, 12000), ("rsmu", 0x33, 12000), ("rsmu", 0x31, 12000)]
# a mailbox that never answers is skipped after the first timeout
q = FakeSmu(rsmu=None); q.apply(dev, 12, False)
assert S.RSMU in q.silent and [m[1] for m in q.msgs] == [0x15, 0x16, 0x14]
# Raven ids and Phoenix's MP1 response register
r = FakeSmu(family=S.detect(cpu(23, 17))); r.apply(dev, 30, False)
assert [m[1] for m in r.msgs] == [0x1B, 0x2F, 0x1C, 0x30, 0x1A, 0x2E]
p = FakeSmu(family=S.detect(cpu(25, 117))); p.apply(dev, 30, False)
assert p.REP == 0x3B10578 and [m[1] for m in p.msgs if m[0] == "mp1"] == [0x15, 0x16, 0x14]
x = FakeSmu(family=S.detect(cpu(26, 36))); x.apply(dev, 28, False)
assert [m[:2] for m in x.msgs] == [("mp1", 0x15), ("rsmu", 0x32), ("mp1", 0x16), ("rsmu", 0x33), ("mp1", 0x14), ("rsmu", 0x31)]
assert x.regs[0x3B10928] == 0x14 and x.regs[0x3B10978] == 1, "Strix MP1 registers"
try:
    FakeSmu(answer=0xFE, rsmu=0xFF).apply(dev, 12, False); raise AssertionError("both refused")
except OSError as e:
    assert "refused" in str(e)
for bad, why in ((FakeSmu(answer=0xFE), "refused"), (FakeSmu(silent=True), "didn't answer")):
    try:
        bad.version(); raise AssertionError(why)
    except OSError as e:
        assert why in str(e), e
print("mailbox protocol, limit order and mW, errors ok")

# the real register access: index / data pair in a PCI config file
tmp = tempfile.mkdtemp(); cfg = f"{tmp}/config"
open(cfg, "wb").write(bytes(256))
real = S(CEZ, cfg)
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
assert S.find(cpu(25, 33)) is None
print("pci index / data access and timeout ok")
print("all amd smu tests passed")
