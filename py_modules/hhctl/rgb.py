"""RGB backends.

- AYANEO NEXT 2: vendor HID (1c4f:0002, usage page 0xFF00), 65-byte report,
  command 21 09 with a checksum in bytes 1-2 (protocol from HHD).
- OneXPlayer: the OXP HID v1 vendor protocol (1a86:fe00, usage page 0xFF00),
  else the hid-oxp kernel LED (`oxp:rgb:*`). On the ONEXPLAYER 3 the kernel
  LED left the lights on rainbow; the vendor protocol reaches every zone
  (light zone test, 2026-10-23).
"""

import colorsys
import glob
import os
import select
import time

from . import sysfs
from .device import Device


def hsv(hue: float, sat: float, val: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb((hue % 360) / 360, sat / 100, val / 100)
    return round(r * 255), round(g * 255), round(b * 255)


def _collections(desc: bytes) -> list[tuple[int, int]]:
    """List (usage_page, usage) of every application collection."""
    out, page, usage, i = [], 0, 0, 0
    while i < len(desc):
        prefix = desc[i]
        size = (0, 1, 2, 4)[prefix & 0x03]
        data = int.from_bytes(desc[i + 1:i + 1 + size], "little")
        tag = prefix & 0xFC
        if tag == 0x04:  # Usage Page
            page = data
        elif tag == 0x08:  # Usage
            usage = data
        elif tag == 0xA0 and data == 0x01:  # Collection (Application)
            out.append((page, usage & 0xFFFF))
        i += 1 + size
    return out


def find_hidraw(vid: int, pid: int, page: int, usage: int = 0x01) -> str | None:
    want = f"{vid:08X}:{pid:08X}"
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = sysfs.read(f"{node}/device/uevent") or ""
        if want not in uevent.upper():
            continue
        try:
            with open(f"{node}/device/report_descriptor", "rb") as f:
                desc = f.read()
        except OSError:
            continue
        if (page, usage) in _collections(desc):
            return f"/dev/{os.path.basename(node)}"
    return None


class _Hidraw:
    def __init__(self, path: str):
        self.path = path

    def send(self, packets: list[bytes], reply_timeout: float = 0.0) -> None:
        fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        try:
            for pkt in packets:
                os.write(fd, pkt)
                deadline = time.monotonic() + reply_timeout
                while reply_timeout and time.monotonic() < deadline:
                    r, _, _ = select.select([fd], [], [], max(0, deadline - time.monotonic()))
                    if not r:
                        break
                    os.read(fd, 128)
                    break
                time.sleep(0.03)
        finally:
            os.close(fd)


class AyaneoHid(_Hidraw):
    name = "ayaneo hid"
    modes = ["off", "solid", "pulse", "rainbow"]
    effects: list[str] = []

    @classmethod
    def find(cls):
        path = find_hidraw(0x1C4F, 0x0002, 0xFF00)
        return cls(path) if path else None

    @staticmethod
    def packet(mode: str, r: int, g: int, b: int) -> bytes:
        # The NEXT 2 ignores mode 0xFF, so "off" is solid black.
        m = {"off": 1, "solid": 1, "pulse": 2, "rainbow": 3}[mode]
        if mode == "off":
            r = g = b = 0
        cmd = [
            0x00,  # report id
            0x00, 0x00,  # checksum
            0x21, 0x09,  # command
            0x00, 0x00, 0x00,
            m, r, g, b,  # right ring
            m, r, g, b,  # left ring
            0x00, 0x00, 0x00, 0x00,
            0x00,  # controller command (none)
            0x00, 0x33,
            0x22,  # joystick sensitivity 100% / 100%
            0x02 << 4,  # vibration: medium (HHD default)
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x01, 0x00, 0x00, 0x00, 0x00,
            0x64, 0x64,
        ]
        buf = bytearray(cmd)
        buf[1:3] = sum(buf[7:]).to_bytes(2, "little")
        return bytes(buf).ljust(65, b"\x00")

    def apply(self, mode: str, hue: int, sat: int, bright: int, effect: str | None) -> None:
        # HHD shifts hue +10 degrees to colour-correct the LEDs.
        h = hue + 10 if mode in ("solid", "pulse") else 275
        r, g, b = hsv(h, sat if mode != "rainbow" else 100, bright)
        self.send([self.packet(mode, r, g, b)], reply_timeout=0.3)


class OxpLed:
    name = "hid-oxp led"
    modes = ["off", "solid", "effect"]

    def __init__(self, path: str):
        self.path = path
        self.effects = [e for e in sysfs.choices(f"{path}/effect_index") if e != "monocolor"]

    @classmethod
    def find(cls):
        for led in sorted(glob.glob("/sys/class/leds/oxp:rgb:*")):
            if os.path.exists(f"{led}/multi_intensity"):
                return cls(led)
        return None

    def apply(self, mode: str, hue: int, sat: int, bright: int, effect: str | None) -> None:
        if mode == "off":
            sysfs.write(f"{self.path}/enabled", "false")
            return
        sysfs.write(f"{self.path}/enabled", "true")
        if mode == "effect" and effect in self.effects:
            sysfs.write(f"{self.path}/effect", effect)
        else:
            sysfs.write(f"{self.path}/effect", "monocolor")
            r, g, b = hsv(hue, sat, 100)
            sysfs.write(f"{self.path}/multi_intensity", f"{r} {g} {b}")
        top = sysfs.read_int(f"{self.path}/max_brightness", 100)
        sysfs.write(f"{self.path}/brightness", round(bright * top / 100))


OXP_V1_EFFECTS = {
    "aurora": 0x01, "flowing": 0x03, "neon": 0x05, "dreamy": 0x07, "sun": 0x08,
    "cyberpunk": 0x09, "sunset": 0x0B, "colorful": 0x0C, "monster_woke": 0x0D,
}


def _oxp_v1_cmd(cid: int, payload: list[int], idx: int = 0x01) -> bytes:
    base = bytes([cid, 0x3F, idx, *payload])
    return base + bytes(64 - len(base) - 2) + bytes([0x3F, cid])


class OxpHidV1(_Hidraw):
    name = "oxp hid"
    modes = ["off", "solid", "effect"]
    effects = list(OXP_V1_EFFECTS)
    # ONEXPLAYER 3 (light zone test): 1 left stick ring, 2 right stick ring,
    # 3 top logo, 5 power and Steam button rings.
    sides = (0x01, 0x02, 0x03, 0x05)

    @classmethod
    def find(cls):
        path = find_hidraw(0x1A86, 0xFE00, 0xFF00)
        return cls(path) if path else None

    def apply(self, mode: str, hue: int, sat: int, bright: int, effect: str | None) -> None:
        on = mode != "off"
        level = 0x01 if bright < 34 else 0x03 if bright < 67 else 0x04
        pkts = [_oxp_v1_cmd(0xB8, [0xFD, s, 0x02, int(on), 0x05, level]) for s in self.sides]
        if on:
            for s in self.sides:
                if mode == "effect" and effect in OXP_V1_EFFECTS:
                    pkts.append(_oxp_v1_cmd(0xB8, [OXP_V1_EFFECTS[effect], s, 0x02]))
                else:
                    r, g, b = hsv(hue, sat, 100)
                    pkts.append(_oxp_v1_cmd(0xB8, [0xFE, s, 0x02] + 18 * [r, g, b] + [r, g]))
        self.send(pkts)


# Light zone test: every candidate zone id gets its own color at once, so the
# user can say which part (stick rings, top logo, power button ring) is which.
ZONE_TEST = [
    (0x01, "red", (255, 0, 0)), (0x02, "green", (0, 255, 0)), (0x03, "blue", (0, 0, 255)),
    (0x04, "yellow", (255, 255, 0)), (0x05, "magenta", (255, 0, 255)), (0x06, "cyan", (0, 255, 255)),
    (0x07, "white", (255, 255, 255)), (0x08, "orange", (255, 100, 0)),
]


def oxp_zone_test() -> str:
    dev = OxpHidV1.find()
    if not dev:
        raise OSError("OneXPlayer vendor HID interface not found")
    pkts = []
    for z, _, (r, g, b) in ZONE_TEST:
        pkts.append(_oxp_v1_cmd(0xB8, [0xFD, z, 0x02, 1, 0x05, 0x04]))
        pkts.append(_oxp_v1_cmd(0xB8, [0xFE, z, 0x02] + 18 * [r, g, b] + [r, g]))
    dev.send(pkts)
    return ", ".join(f"zone {z}: {name}" for z, name, _ in ZONE_TEST)


def find_backend(dev: Device):
    if dev.rgb == "ayaneo_hid":
        return AyaneoHid.find()
    if dev.rgb == "oxp":
        return OxpHidV1.find() or OxpLed.find()
    return OxpLed.find()
