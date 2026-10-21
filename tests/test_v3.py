import sys, os, tempfile, time, json, types, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import ec, gpu, kargs, backlight, watchdog, sysfs

# ---- simulated ACPI EC behind ports 0x62/0x66
class SimEC:
    def __init__(self):
        self.ram = bytearray(256); self.status = 0; self.data = 0; self.state = None
    def inp(self, port):
        if port == 0x66: return self.status
        self.status &= ~0x01; return self.data
    def out(self, port, v):
        if port == 0x66: self.state = ("rd",) if v == 0x80 else ("wr",); return
        st = self.state
        if st == ("rd",): self.data = self.ram[v]; self.status |= 0x01; self.state = None
        elif st == ("wr",): self.state = ("wr", v)
        elif st and st[0] == "wr": self.ram[st[1]] = v; self.state = None
sim = SimEC(); sim.ram[0x1E] = 0xAA; sim.ram[0x4A] = 0
class FakePort(ec.PortIo):
    def __init__(self): pass
    def _in(self, p): return sim.inp(p)
    def _out(self, p, v): sim.out(p, v)
ec._transport = FakePort()
assert ec.read(0x1E) == 0xAA
ec.write(0x1E, 0x55, {0xAA, 0x55}); assert sim.ram[0x1E] == 0x55
sim.status |= 0x01; sim.data = 0x99            # stale byte left by someone else
FakePort.TIMEOUT = 0.01
assert ec.read(0x4A) == 0
print("port protocol: read/write/guard/stale-OBF ok")

# ---- MMIO cross-check
class FakeMM:
    def __init__(self, base): self.ram = bytearray(sim.ram)
    def read_byte(self, r): return self.ram[r]
ec.Mmio = FakeMM
assert ec._try_mmio(FakePort(), 0xFE800400, (0x1E, 0x4A)) is not None
class BadMM(FakeMM):
    def read_byte(self, r): return 0xFF
ec.Mmio = BadMM
assert ec._try_mmio(FakePort(), 0xFE800400, (0x1E, 0x4A)) is None
print("mmio cross-check: accepts mirror, rejects mismatch")

# ---- watchdog port restore through a shimmed os
sim.ram[0x4A] = 1; sim.status |= 0x01; sim.data = 0x77  # stale byte here too
shim = types.SimpleNamespace(
    O_RDWR=2, open=lambda *a, **k: 3, close=lambda fd: None,
    pread=lambda fd, n, off: bytes([sim.inp(off)]),
    pwrite=lambda fd, b, off: sim.out(off, b[0]),
)
real_os = watchdog.os; watchdog.os = shim
watchdog.restore({"kind": "ec", "transport": "port", "mode_reg": 0x4A, "manual": 1, "auto": 0})
watchdog.os = real_os
assert sim.ram[0x4A] == 0
print("watchdog port restore ok")

# ---- kargs: Limine
tmp = tempfile.mkdtemp()
lim = os.path.join(tmp, "limine")
orig = 'TARGET_OS_NAME="CachyOS"\nKERNEL_CMDLINE[default]="quiet nowatchdog splash rw rootflags=subvol=/@ root=UUID=c988 xe.enable_psr=1"\nENABLE_UKI=yes\n'
open(lim, "w").write(orig)
bl = kargs.FileBootloader("Limine", lim, "KERNEL_CMDLINE[default]", [["true"]])
bl.set(["xe.enable_psr=0", "xe.enable_panel_replay=0"], set())
txt = open(lim).read()
assert 'KERNEL_CMDLINE[default]="quiet nowatchdog splash rw rootflags=subvol=/@ root=UUID=c988 xe.enable_psr=0 xe.enable_panel_replay=0"' in txt, txt
assert txt.startswith('TARGET_OS_NAME="CachyOS"\n') and txt.endswith("ENABLE_UKI=yes\n")
assert open(lim + ".hhctl-orig").read() == orig
assert {"xe.enable_psr=0", "xe.enable_panel_replay=0"} <= set(bl.configured())
bl.set(["xe.enable_dc=0"], set()); bl.set([], {"xe.enable_psr", "xe.enable_panel_replay"})
assert bl.configured()[-1] == "xe.enable_dc=0" and "xe.enable_psr=0" not in bl.configured()
before = open(lim).read()
bad = kargs.FileBootloader("Limine", lim, "KERNEL_CMDLINE[default]", [["false"]])
try: bad.set(["xe.enable_psr=0"], set()); raise SystemExit("should fail")
except OSError as e: print("regen failure restored config:", e)
assert open(lim).read() == before
# GRUB with single quotes, sdboot create
g = os.path.join(tmp, "grub"); open(g, "w").write("GRUB_TIMEOUT=5\nGRUB_CMDLINE_LINUX_DEFAULT='quiet splash'\n")
kargs.FileBootloader("GRUB", g, "GRUB_CMDLINE_LINUX_DEFAULT", [["true"]]).set(["i915.enable_psr=0"], set())
assert "GRUB_CMDLINE_LINUX_DEFAULT='quiet splash i915.enable_psr=0'" in open(g).read()
sd = os.path.join(tmp, "sd"); open(sd, "w").write('#LINUX_OPTIONS=""\n')
kargs.FileBootloader("sd", sd, "LINUX_OPTIONS", [["true"]], create=True).set(["xe.enable_dc=0"], set())
assert open(sd).read() == '#LINUX_OPTIONS=""\nLINUX_OPTIONS="xe.enable_dc=0"\n'
print("kargs: limine/grub/sdboot edit, backup, rollback ok")

# ---- fdinfo parsing
fd1 = os.path.join(tmp, "f1"); fd2 = os.path.join(tmp, "f2"); fd3 = os.path.join(tmp, "f3")
open(fd1, "w").write("pos:\t0\ndrm-driver:\txe\ndrm-pdev:\t0000:00:02.0\ndrm-client-id:\t7\ndrm-resident-system:\t512 MiB\ndrm-resident-gtt:\t1024 KiB\n")
open(fd2, "w").write(open(fd1).read())  # same client, second fd
open(fd3, "w").write("drm-driver:\txe\ndrm-pdev:\t0000:00:02.0\ndrm-client-id:\t9\ndrm-resident-system:\t1 GiB\n")
real_glob = gpu.glob.glob
gpu.glob = types.SimpleNamespace(glob=lambda pat: [fd1, fd2, fd3] if "fdinfo" in pat else real_glob(pat))
assert gpu.drm_client_memory("xe") == (512 << 20) + (1024 << 10) + (1 << 30)
assert gpu.drm_client_memory("i915") is None
gpu.glob = types.SimpleNamespace(glob=real_glob)
print("fdinfo memory sum ok (dedupes clients)")

# ---- live read on this machine
g = gpu.Gpu(); g.sample(); time.sleep(0.5); g.sample()
print("live gpu:", g.state())
b = backlight.Backlight.find()
print("live backlight:", b and (b.name, b.percent()))
shutil.rmtree(tmp); print("all v0.3 tests passed")
