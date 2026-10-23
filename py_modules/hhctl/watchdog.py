"""Fan watchdog, run as its own systemd unit so it outlives the plugin.

While the plugin holds the fan in manual mode it keeps a state file and a
heartbeat in RUN_DIR. If the heartbeat goes stale (plugin crashed, hung or
was killed) this puts the fan back under firmware control and exits. If the
state file disappears, the plugin returned the fan to auto itself, so the
watchdog just exits.

Standalone on purpose: no imports from the plugin package.
"""

import json
import mmap
import os
import sys
import time

RUN_DIR = "/run/hhctl"
STATE = f"{RUN_DIR}/fan-watchdog.json"
HEARTBEAT = f"{RUN_DIR}/heartbeat"
PIDFILE = f"{RUN_DIR}/watchdog.pid"
EC_IO = "/sys/kernel/debug/ec/ec0/io"
STALE = 20.0  # seconds of CLOCK_MONOTONIC, which does not advance in suspend
POLL = 3.0


def _port_xfer(fd: int, cmd: int, reg: int, value: int | None = None) -> int | None:
    """Minimal ACPI EC transaction on 0x62/0x66 (see ec.PortIo)."""
    def status():
        return os.pread(fd, 1, 0x66)[0]

    def wait(ready):
        deadline = time.monotonic() + 0.2
        while not ready(status()):
            if time.monotonic() > deadline:
                raise OSError("EC port handshake timed out")
            time.sleep(0.0002)

    wait(lambda s: not s & 0x02)
    try:
        wait(lambda s: not s & 0x01)
    except OSError:
        os.pread(fd, 1, 0x62)  # discard a stale byte nobody collected
    os.pwrite(fd, bytes([cmd]), 0x66)
    wait(lambda s: not s & 0x02)
    os.pwrite(fd, bytes([reg]), 0x62)
    if value is None:
        wait(lambda s: s & 0x01)
        return os.pread(fd, 1, 0x62)[0]
    wait(lambda s: not s & 0x02)
    os.pwrite(fd, bytes([value]), 0x62)
    wait(lambda s: not s & 0x02)
    return None


def _ec_restore(spec: dict) -> None:
    reg, manual, auto = spec["mode_reg"], spec["manual"], spec["auto"]
    transport = spec.get("transport", "ec_sys")
    # Only flip the register if it still holds our manual value.
    if transport == "port":
        fd = os.open("/dev/port", os.O_RDWR)
        try:
            if _port_xfer(fd, 0x80, reg) == manual:
                _port_xfer(fd, 0x81, reg, auto)
        finally:
            os.close(fd)
    elif transport == "mmio":
        base = spec["base"]
        page = base & ~(mmap.PAGESIZE - 1)
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        try:
            mm = mmap.mmap(fd, mmap.PAGESIZE, mmap.MAP_SHARED,
                           mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
        finally:
            os.close(fd)
        if mm[base - page + reg] == manual:
            mm[base - page + reg] = auto
        mm.close()
    else:
        with open(spec.get("path", EC_IO), "r+b", buffering=0) as f:
            f.seek(reg)
            if f.read(1)[0] == manual:
                f.seek(reg)
                f.write(bytes([auto]))


def restore(spec: dict) -> None:
    if spec["kind"] == "hwmon":
        for n in spec.get("channels", [1]):
            with open(f"{spec['path']}/pwm{n}_enable", "w") as f:
                f.write(f"{spec.get('auto', 2)}\n")
    elif spec["kind"] == "ec":
        _ec_restore(spec)


def heartbeat_age() -> float:
    try:
        with open(HEARTBEAT) as f:
            return time.monotonic() - float(f.read())
    except (OSError, ValueError):
        return float("inf")


def main() -> int:
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    try:
        while True:
            try:
                with open(STATE) as f:
                    spec = json.load(f)
            except FileNotFoundError:
                return 0
            except (OSError, ValueError):
                spec = None
            if spec and heartbeat_age() > STALE:
                print("hhctl: plugin heartbeat stale, restoring fan auto", file=sys.stderr)
                try:
                    restore(spec)
                except OSError as e:
                    print(f"hhctl: restore failed, retrying: {e}", file=sys.stderr)
                    time.sleep(POLL)
                    continue
                os.unlink(STATE)
                return 0
            time.sleep(POLL)
    finally:
        try:
            os.unlink(PIDFILE)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
