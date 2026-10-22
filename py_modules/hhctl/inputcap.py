"""Controller input recorder, runnable from the panel (no SSH needed).

Lists every input and hidraw device, then records what they send for a few
seconds while the user presses the extra buttons and touches the touchpads.
The report is what the controller remapping is built from: which device each
button lives on (gamepad, keyboard, vendor HID) and what it sends.

Read-only: devices are opened without EVIOCGRAB, so the system and Steam keep
receiving every event while recording.
"""

import glob
import os
import platform
import select
import struct
import time

from . import sysfs

SYS_INPUT = "/sys/class/input"
SYS_HIDRAW = "/sys/class/hidraw"
DEV = "/dev"
EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit
EV_NAMES = {0x01: "KEY", 0x02: "REL", 0x03: "ABS", 0x04: "MSC", 0x05: "SW", 0x11: "LED", 0x15: "FF"}
SKIP_TYPES = {0x00, 0x11, 0x15}  # SYN, LED, FF feedback
ABS_PER_CODE = 30  # touchpads and sticks are chatty: keep the first N per axis
MAX_PER_DEVICE = 500
HID_MAX_PER_DEVICE = 300
DESC_MAX = 4096

# Enough names to read the report at a glance; everything also has its number.
KEY_NAMES = {
    1: "ESC", 15: "TAB", 28: "ENTER", 29: "LEFTCTRL", 32: "D", 42: "LEFTSHIFT", 56: "LEFTALT",
    97: "RIGHTCTRL", 100: "RIGHTALT", 125: "LEFTMETA", 126: "RIGHTMETA", 127: "COMPOSE",
    139: "MENU", 158: "BACK", 172: "HOMEPAGE",
    **{59 + i: f"F{i + 1}" for i in range(10)}, 87: "F11", 88: "F12",
    **{183 + i: f"F{13 + i}" for i in range(12)},
    0x110: "BTN_LEFT", 0x111: "BTN_RIGHT", 0x112: "BTN_MIDDLE",
    0x130: "BTN_A", 0x131: "BTN_B", 0x133: "BTN_X", 0x134: "BTN_Y", 0x136: "BTN_TL", 0x137: "BTN_TR",
    0x138: "BTN_TL2", 0x139: "BTN_TR2", 0x13A: "BTN_SELECT", 0x13B: "BTN_START", 0x13C: "BTN_MODE",
    0x13D: "BTN_THUMBL", 0x13E: "BTN_THUMBR", 0x145: "BTN_TOOL_FINGER", 0x14A: "BTN_TOUCH",
    0x14D: "BTN_TOOL_DOUBLETAP", 0x2C0: "BTN_TRIGGER_HAPPY1",
    **{0x2C0 + i: f"BTN_TRIGGER_HAPPY{i + 1}" for i in range(1, 40)},
}
ABS_NAMES = {
    0x00: "X", 0x01: "Y", 0x02: "Z", 0x03: "RX", 0x04: "RY", 0x05: "RZ", 0x10: "HAT0X", 0x11: "HAT0Y",
    0x2F: "MT_SLOT", 0x35: "MT_POSITION_X", 0x36: "MT_POSITION_Y", 0x39: "MT_TRACKING_ID",
}


def _name(etype: int, code: int) -> str:
    names = {0x01: KEY_NAMES, 0x03: ABS_NAMES}.get(etype, {})
    return f"{EV_NAMES.get(etype, hex(etype))} {code}" + (f" ({names[code]})" if code in names else "")


def _event_devices() -> list[str]:
    return sorted(glob.glob(f"{SYS_INPUT}/event[0-9]*"), key=lambda p: int(p.rsplit("event", 1)[1]))


def _hidraw_devices() -> list[str]:
    return sorted(glob.glob(f"{SYS_HIDRAW}/hidraw[0-9]*"), key=lambda p: int(p.rsplit("hidraw", 1)[1]))


def _uevent(path: str) -> dict[str, str]:
    out = {}
    for line in (sysfs.read(path) or "").splitlines():
        k, _, v = line.partition("=")
        out[k] = v
    return out


def devices() -> list[str]:
    """Static description of every input and hidraw device."""
    lines = [f"kernel: {platform.release()}"]
    for k in ("sys_vendor", "product_name", "board_vendor", "board_name"):
        lines.append(f"dmi {k}: {sysfs.read(f'/sys/class/dmi/id/{k}')}")
    lines.append("hid/input modules: " + " ".join(
        sorted(m for m in os.listdir("/sys/module") if m.startswith(("hid", "xpad", "oxp", "ayaneo", "ayn")))
        if os.path.isdir("/sys/module") else []
    ))
    for ev in _event_devices():
        d = f"{ev}/device"
        ids = "/".join(sysfs.read(f"{d}/id/{k}") or "?" for k in ("bustype", "vendor", "product", "version"))
        lines.append(f"--- {os.path.basename(ev)}: '{sysfs.read(f'{d}/name')}' id={ids}")
        lines.append(f"    phys={sysfs.read(f'{d}/phys')} uniq={sysfs.read(f'{d}/uniq')}")
        lines.append(f"    sysfs={os.path.realpath(d)}")
        caps = {k: sysfs.read(f"{d}/capabilities/{k}") for k in ("ev", "key", "abs", "rel", "msc", "sw")}
        lines.append("    caps " + " ".join(f"{k}=[{v}]" for k, v in caps.items() if v and v != "0"))
        props = sysfs.read(f"{d}/properties")
        if props and props != "0":
            lines.append(f"    properties={props}")
    for hr in _hidraw_devices():
        u = _uevent(f"{hr}/device/uevent")
        lines.append(f"--- {os.path.basename(hr)}: '{u.get('HID_NAME')}' id={u.get('HID_ID')} phys={u.get('HID_PHYS')}")
        lines.append(f"    driver={u.get('DRIVER')} sysfs={os.path.realpath(f'{hr}/device')}")
        try:
            with open(f"{hr}/device/report_descriptor", "rb") as f:
                desc = f.read(DESC_MAX)
            lines.append(f"    report descriptor ({len(desc)} bytes): {desc.hex()}")
        except OSError as e:
            lines.append(f"    report descriptor: {e}")
    return lines


def record(seconds: float, progress=None) -> tuple[list[str], dict[str, int]]:
    """Record events from all devices for `seconds`.

    Returns (log lines, {device: number of button/key events}). `progress` is
    called with the seconds left, about once a second.
    """
    fds: dict[int, str] = {}
    kinds: dict[int, str] = {}
    errors = []
    for ev in _event_devices():
        node = f"{DEV}/input/{os.path.basename(ev)}"
        try:
            fds[os.open(node, os.O_RDONLY | os.O_NONBLOCK)] = os.path.basename(ev)
        except OSError as e:
            errors.append(f"{node}: {e}")
    for hr in _hidraw_devices():
        node = f"{DEV}/{os.path.basename(hr)}"
        try:
            fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
            fds[fd] = os.path.basename(hr)
            kinds[fd] = "hid"
        except OSError as e:
            errors.append(f"{node}: {e}")

    lines = [f"cannot open {e}" for e in errors]
    count: dict[str, int] = {}
    abs_seen: dict[tuple[str, int], int] = {}
    last_hid: dict[str, bytes] = {}
    buttons: dict[str, int] = {}
    t0 = time.monotonic()
    end = t0 + seconds
    next_tick = 0.0
    try:
        while (now := time.monotonic()) < end:
            if progress and now >= next_tick:
                progress(int(end - now + 0.999))
                next_tick = now + 1
            ready, _, _ = select.select(list(fds), [], [], min(0.5, end - now))
            for fd in ready:
                dev = fds[fd]
                try:
                    data = os.read(fd, EVENT.size * 64 if fd not in kinds else 4096)
                except BlockingIOError:
                    continue
                except OSError as e:
                    lines.append(f"{time.monotonic() - t0:7.3f} {dev}: read failed ({e}), dropped")
                    os.close(fd)
                    del fds[fd]
                    continue
                if not data:  # device went away
                    lines.append(f"{time.monotonic() - t0:7.3f} {dev}: closed, dropped")
                    os.close(fd)
                    del fds[fd]
                    continue
                stamp = f"{time.monotonic() - t0:7.3f} {dev}"
                if fd in kinds:
                    if data == last_hid.get(dev) or count.get(dev, 0) >= HID_MAX_PER_DEVICE:
                        continue
                    last_hid[dev] = data
                    count[dev] = count.get(dev, 0) + 1
                    lines.append(f"{stamp} hid {data.hex()}")
                    continue
                for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, etype, code, value = EVENT.unpack_from(data, off)
                    if etype in SKIP_TYPES or count.get(dev, 0) >= MAX_PER_DEVICE:
                        continue
                    if etype == 0x03:
                        n = abs_seen.get((dev, code), 0)
                        abs_seen[(dev, code)] = n + 1
                        if n >= ABS_PER_CODE:
                            continue
                    if etype == 0x01:
                        buttons[dev] = buttons.get(dev, 0) + 1
                    count[dev] = count.get(dev, 0) + 1
                    lines.append(f"{stamp} {_name(etype, code)} = {value}")
    finally:
        for fd in fds:
            os.close(fd)
    for (dev, code), n in sorted(abs_seen.items()):
        if n > ABS_PER_CODE:
            lines.append(f"{dev}: {_name(0x03, code)} sent {n} events, first {ABS_PER_CODE} shown")
    for dev, n in sorted(count.items()):
        if n >= (HID_MAX_PER_DEVICE if dev.startswith("hidraw") else MAX_PER_DEVICE):
            lines.append(f"{dev}: stopped logging after {n} events")
    return lines or ["no events"], buttons
