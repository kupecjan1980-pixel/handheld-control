"""Raw embedded controller access.

Only used when no kernel driver (ayaneo-ec / oxpec) is bound for the device,
and only after the user enables EC writes. Transports, in order:

1. ec_sys debugfs (/sys/kernel/debug/ec/ec0/io). The kernel serialises it
   with its own EC traffic, so it is the safest.
2. Memory-mapped EC RAM through /dev/mem, for devices whose EC RAM is
   mirrored in MMIO (AYANEO NEXT 2). Used only if it matches port reads.
3. ACPI EC port protocol on 0x62/0x66 through /dev/port. Not serialised
   with the kernel's EC driver, so every transaction is retried on timeout
   and all writes are verified.

Every write is guarded: the register must hold a value from its known set
before we touch it, and must read back as written afterwards. All of this is
blocked when the kernel is locked down (Secure Boot on Fedora/Bazzite).
"""

import mmap
import os
import time

from . import proc, sysfs

EC_IO = "/sys/kernel/debug/ec/ec0/io"
WRITE_PARAM = "/sys/module/ec_sys/parameters/write_support"

EC_DATA, EC_SC = 0x62, 0x66
OBF, IBF = 0x01, 0x02
RD_EC, WR_EC = 0x80, 0x81


class EcError(OSError):
    """The EC did not look or behave like the register map says."""


class EcSys:
    name = "ec_sys"

    def __init__(self, path: str = EC_IO):
        self.path = path

    def read_byte(self, reg: int) -> int:
        with open(self.path, "rb") as f:
            f.seek(reg)
            return f.read(1)[0]

    def write_byte(self, reg: int, value: int) -> None:
        with open(self.path, "r+b", buffering=0) as f:
            f.seek(reg)
            f.write(bytes([value]))

    def spec(self) -> dict:
        return {"transport": "ec_sys", "path": self.path}


class PortIo:
    name = "port I/O"
    TIMEOUT = 0.2
    RETRIES = 3

    def __init__(self):
        self.fd = os.open("/dev/port", os.O_RDWR)

    def _in(self, port: int) -> int:
        return os.pread(self.fd, 1, port)[0]

    def _out(self, port: int, value: int) -> None:
        os.pwrite(self.fd, bytes([value]), port)

    def _wait(self, ready) -> None:
        deadline = time.monotonic() + self.TIMEOUT
        while not ready(self._in(EC_SC)):
            if time.monotonic() > deadline:
                raise TimeoutError("EC port handshake timed out")
            time.sleep(0.0002)

    def _settle(self) -> None:
        """Wait for any transaction in flight (ours or the kernel's)."""
        self._wait(lambda s: not s & IBF)
        try:
            self._wait(lambda s: not s & OBF)
        except TimeoutError:
            self._in(EC_DATA)  # discard a stale byte nobody collected

    def _retry(self, fn):
        for attempt in range(self.RETRIES):
            try:
                return fn()
            except TimeoutError:
                if attempt == self.RETRIES - 1:
                    raise EcError("EC did not respond on ports 0x62/0x66")
                time.sleep(0.01)

    def read_byte(self, reg: int) -> int:
        def go():
            self._settle()
            self._out(EC_SC, RD_EC)
            self._wait(lambda s: not s & IBF)
            self._out(EC_DATA, reg)
            self._wait(lambda s: s & OBF)
            return self._in(EC_DATA)
        return self._retry(go)

    def write_byte(self, reg: int, value: int) -> None:
        def go():
            self._settle()
            self._out(EC_SC, WR_EC)
            self._wait(lambda s: not s & IBF)
            self._out(EC_DATA, reg)
            self._wait(lambda s: not s & IBF)
            self._out(EC_DATA, value)
            self._wait(lambda s: not s & IBF)
        self._retry(go)

    def spec(self) -> dict:
        return {"transport": "port"}


class Mmio:
    name = "MMIO"

    def __init__(self, base: int):
        self.base = base
        page = base & ~(mmap.PAGESIZE - 1)
        self.off = base - page
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        try:
            self.mm = mmap.mmap(fd, mmap.PAGESIZE, mmap.MAP_SHARED,
                                mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
        finally:
            os.close(fd)

    def read_byte(self, reg: int) -> int:
        return self.mm[self.off + reg]

    def write_byte(self, reg: int, value: int) -> None:
        self.mm[self.off + reg] = value

    def spec(self) -> dict:
        return {"transport": "mmio", "base": self.base}


_transport = None


def lockdown() -> str | None:
    return sysfs.selected("/sys/kernel/security/lockdown")


def _try_ec_sys() -> tuple[EcSys | None, str]:
    if not os.path.ismount("/sys/kernel/debug"):
        proc.run(["mount", "-t", "debugfs", "none", "/sys/kernel/debug"], timeout=10)
    if not os.path.exists(EC_IO):
        proc.run(["modprobe", "ec_sys", "write_support=1"], timeout=10)
    if not os.path.exists(EC_IO):
        return None, "ec_sys is not available in this kernel"
    if sysfs.read(WRITE_PARAM) not in ("Y", "1"):
        try:
            sysfs.write(WRITE_PARAM, "Y")
        except OSError:
            pass
    if sysfs.read(WRITE_PARAM) not in ("Y", "1"):
        return None, "ec_sys loaded without write_support=1"
    return EcSys(), "ec_sys"


def _try_mmio(port: PortIo, base: int, check_regs) -> Mmio | None:
    """Use MMIO only if it mirrors the EC exactly on stable registers."""
    try:
        mm = Mmio(base)
    except OSError:
        return None
    try:
        pairs = [(port.read_byte(r), mm.read_byte(r)) for r in check_regs]
    except OSError:
        return None
    if all(a == b for a, b in pairs) and any(a not in (0x00, 0xFF) for a, _ in pairs):
        return mm
    return None


def available(mmio_base: int | None = None, check_regs=()) -> tuple[bool, str]:
    global _transport
    if _transport:
        return True, _transport.name
    mode = lockdown()
    if mode and mode != "none":
        return False, f"kernel lockdown is '{mode}' (Secure Boot); raw EC access is blocked"
    t, why = _try_ec_sys()
    if t:
        _transport = t
        return True, t.name
    try:
        port = PortIo()
        port.read_byte(check_regs[0] if check_regs else 0x00)
    except OSError as e:
        return False, f"{why}; /dev/port unusable ({e})"
    if mmio_base is not None and check_regs:
        mm = _try_mmio(port, mmio_base, check_regs)
        if mm:
            _transport = mm
            return True, mm.name
    _transport = port
    return True, port.name


def transport_spec() -> dict:
    return _transport.spec() if _transport else {}


def read(reg: int, size: int = 1) -> int:
    """Read `size` bytes big-endian, as oxpec/ayaneo-ec do for tach values."""
    val = 0
    for i in range(size):
        val = (val << 8) | _transport.read_byte(reg + i)
    return val


def _write(reg: int, value: int) -> None:
    _transport.write_byte(reg, value & 0xFF)


def check(reg: int, allowed) -> int:
    """Return the register value, or raise if it is outside `allowed`."""
    cur = read(reg)
    if cur not in allowed:
        raise EcError(
            f"EC register 0x{reg:02X} reads 0x{cur:02X}, which the register map "
            "does not expect; EC control disabled"
        )
    return cur


def write(reg: int, value: int, allowed) -> None:
    """Guarded write: validate the current value, write, then read back."""
    if check(reg, allowed) == value:
        return
    _write(reg, value)
    back = read(reg)
    if back != value:
        raise EcError(
            f"EC register 0x{reg:02X} read back 0x{back:02X} after writing "
            f"0x{value:02X}; EC control disabled"
        )
