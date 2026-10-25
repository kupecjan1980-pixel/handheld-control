"""Device detection and per-device hardware descriptions.

Register maps come from the upstream kernel drivers:
- AYANEO NEXT 2 (board AB09, vendor "AYA"): same EC layout as ayaneo-ec's
  quirk_charge_limit devices (fan 0x4A/0x4B, tach 0x76, charge 0x1E).
- ONEXPLAYER 3: mapped to oxp_g1_i in oxpec (pdx86 for-next).
"""

import re
from dataclasses import dataclass, field

from . import sysfs

DMI = "/sys/class/dmi/id"


@dataclass
class EcFan:
    mode_reg: int
    manual: int
    auto: int
    pwm_reg: int
    pwm_max: int  # EC duty range is 0..pwm_max
    tach_reg: int

    def mode_values(self) -> set[int]:
        return {self.manual, self.auto}

    def pwm_values(self) -> range:
        return range(self.pwm_max + 1)


@dataclass
class EcCharge:
    # AYANEO style: one register, auto/inhibit magic values, no % limit
    behaviour_reg: int
    auto: int
    inhibit: int
    inhibit_awake: int | None = None
    # OneXPlayer style: native % threshold register
    limit_reg: int | None = None
    # Other values the behaviour register may legitimately hold
    behaviour_extra: tuple[int, ...] = ()

    def behaviour_values(self) -> set[int]:
        vals = {self.auto, self.inhibit, *self.behaviour_extra}
        if self.inhibit_awake is not None:
            vals.add(self.inhibit_awake)
        return vals

    @staticmethod
    def limit_values() -> range:
        return range(101)


@dataclass
class Device:
    key: str
    name: str
    cpu: str  # "amd" | "intel"
    tdp_min: int
    tdp_max: int
    tdp_default: int
    tdp_boost_max: int  # ceiling for PL2/SPPT/FPPT
    hwmon_names: tuple[str, ...] = ()
    ec_fan: EcFan | None = None
    ec_charge: EcCharge | None = None
    rgb: str | None = None  # "ayaneo_hid" | "oxp" | None
    ec_mmio: int | None = None  # physical address of memory-mapped EC RAM
    notes: list[str] = field(default_factory=list)
    # Display toggles that must not be turned on for this device, with why.
    display_blocked: dict[str, str] = field(default_factory=dict)
    # Why Game Mode HDR must be left to Steam on this device, if it must.
    gamescope_hdr_blocked: str | None = None
    board: str = ""
    vendor: str = ""

    def ec_check_regs(self) -> tuple[int, ...]:
        """Stable registers used to probe and cross-check EC transports."""
        regs = []
        if self.ec_charge:
            regs.append(self.ec_charge.behaviour_reg)
        if self.ec_fan:
            regs.append(self.ec_fan.mode_reg)
        return tuple(regs)


def _next2(vendor: str, board: str) -> Device:
    return Device(
        key="ayaneo_next2",
        name="AYANEO NEXT 2",
        cpu="amd",
        # BIOS custom ceiling read back as 80/82/85 W; AYANEO rates 85 W.
        tdp_min=10,
        tdp_max=80,
        tdp_default=30,
        tdp_boost_max=85,
        hwmon_names=("ayaneo_ec",),
        ec_fan=EcFan(
            mode_reg=0x4A, manual=0x01, auto=0x00,
            pwm_reg=0x4B, pwm_max=100, tach_reg=0x76,
        ),
        ec_charge=EcCharge(
            behaviour_reg=0x1E, auto=0xAA, inhibit=0x55,
            # 0x00 until something sets it after power-on; ayaneo-ec treats
            # every value but 0x55 as auto (seen on a NEXT 2, 2026-10-21).
            behaviour_extra=(0x00,),
        ),
        rgb="ayaneo_hid",
        # From a NEXT 2 EC map: ports 0x62/0x66 mirror this window. Only
        # used after it matches port reads register for register.
        ec_mmio=0xFE800400,
        notes=[
            "The NEXT 2 has no charge limit of its own: the plugin pauses "
            "charging at the limit, so it only holds while the plugin runs.",
        ],
        board=board,
        vendor=vendor,
    )


def _oxp3(vendor: str, board: str) -> Device:
    return Device(
        key="oxp3",
        name="ONEXPLAYER 3",
        cpu="intel",
        tdp_min=3,
        tdp_max=40,
        tdp_default=25,
        tdp_boost_max=42,
        hwmon_names=("oxp_ec",),
        ec_fan=EcFan(
            mode_reg=0x4A, manual=0x01, auto=0x00,
            pwm_reg=0x4B, pwm_max=184, tach_reg=0x58,
        ),
        ec_charge=EcCharge(
            behaviour_reg=0xA4, auto=0x00, inhibit=0x03, inhibit_awake=0x01,
            limit_reg=0xA3,
            behaviour_extra=(0x02,),  # oxpec's "inhibit while off" bit alone
        ),
        rgb="oxp",
        display_blocked={
            "psr": "Blocked on this device: with PSR and Panel Replay off, the "
                   "ONEXPLAYER 3's panel stays black after boot.",
            "dc": "Blocked on this device: untested on this panel, and turning "
                  "off PSR already blanked it.",
        },
        gamescope_hdr_blocked=(
            "Blocked on this device: in SDR, or when switching between HDR and "
            "SDR, the ONEXPLAYER 3's screen turns off and only a long power "
            "press brings it back. HDR (Steam's default) keeps it stable."
        ),
        board=board,
        vendor=vendor,
    )


# Placeholders boards ship with instead of a real name.
DMI_JUNK = {"", "default string", "to be filled by o.e.m.", "system product name", "system manufacturer", "o.e.m.", "oem"}


def cpu_model(cpuinfo: str | None = None) -> str | None:
    """'AMD Ryzen 7 PRO 7730U with Radeon Graphics' -> 'Ryzen 7 PRO 7730U'."""
    if cpuinfo is None:
        cpuinfo = sysfs.read("/proc/cpuinfo") or ""
    for line in cpuinfo.splitlines():
        if line.startswith("model name"):
            name = re.sub(r"\s+w(ith|/)\s+Radeon.*", "", line.split(":", 1)[1]).strip()
            name = re.sub(r"\s+\d+-Core Processor$", "", name)
            name = re.sub(r"\((R|TM)\)", "", name)
            return re.sub(r"^(AMD|Intel)\s+", "", name).strip() or None
    return None


# Vendors whose product names already carry the brand ("ONEXPLAYER F1 EVA-02").
VENDOR_IN_PRODUCT = {"one-netbook"}


def _real(val: str | None) -> str:
    return "" if (val or "").strip().lower() in DMI_JUNK else (val or "").strip()


# PCs whose BIOS left every name field as a placeholder, told apart by BIOS
# date + version + CPU instead. Only the shown name comes from here.
KNOWN_UNNAMED = [
    # GenMachine Ren7000 mini PC (Ryzen 7 PRO 7730U), all DMI names "Default string".
    {"bios_date": "04/15/2023", "bios_version": "5.19", "cpu": "7730U", "name": "GenMachine Ren7000"},
]


def _known_unnamed(cpuinfo: str) -> str | None:
    date, ver = sysfs.read(f"{DMI}/bios_date"), sysfs.read(f"{DMI}/bios_version")
    for k in KNOWN_UNNAMED:
        if (date, ver) == (k["bios_date"], k["bios_version"]) and k["cpu"] in cpuinfo:
            return k["name"]
    return None


def _generic_name(vendor: str, board: str, cpuinfo: str) -> str:
    """The firmware's (BIOS) name: vendor + product ("ASRock DeskMini X600"),
    else vendor + board ("ASRock X600M-STX"), else the CPU model when every
    field is a placeholder (GenMachine Ren: "Default string")."""
    sys_vendor, product = _real(sysfs.read(f"{DMI}/sys_vendor")), _real(sysfs.read(f"{DMI}/product_name"))
    if product:
        v = sys_vendor or _real(vendor)
        skip = not v or v.lower() in VENDOR_IN_PRODUCT or v.split()[0].lower() in product.lower()
        return product if skip else f"{v} {product}"
    parts = [p for p in (_real(vendor), _real(board)) if p]
    if parts:
        return " ".join(parts)
    known = _known_unnamed(cpuinfo)
    if known:
        return known
    for line in cpuinfo.splitlines():
        if line.startswith("model name"):
            # "... with Radeon Graphics", "... w/ Radeon 780M Graphics"
            return re.sub(r"\s+w(ith|/)\s+Radeon.*", "", line.split(":", 1)[1]).strip()
    return "Unknown device"


def desktop_apu_limits(cpuinfo: str) -> tuple[int, int, int, int] | None:
    """(min, max, default, boost max) W for AM4/AM5 G-series APUs, from the
    model name: 65 W parts (5700G, 8700G) allow 88 W PPT, 35 W parts (GE,
    Athlon 3000G) 35 W."""
    name = next((l.split(":", 1)[1] for l in cpuinfo.splitlines() if l.startswith("model name")), "")
    m = re.search(r"\b\d{4}G(E)?\b", name)
    if not m:
        return None
    if m.group(1) or "Athlon" in name:
        return 5, 35, 35, 35
    return 10, 88, 65, 88


def detect() -> Device:
    vendor = sysfs.read(f"{DMI}/board_vendor") or sysfs.read(f"{DMI}/sys_vendor") or ""
    board = sysfs.read(f"{DMI}/board_name") or ""
    if vendor in ("AYA", "AYANEO") and board == "AB09":
        return _next2(vendor, board)
    if vendor == "ONE-NETBOOK" and board == "ONEXPLAYER 3":
        return _oxp3(vendor, board)

    # Unknown device: only use kernel interfaces, never raw EC writes.
    cpuinfo = sysfs.read("/proc/cpuinfo") or ""
    cpu = "intel" if "GenuineIntel" in cpuinfo else "amd"
    notes = ["Not one of the supported handhelds, so only standard kernel interfaces are used."]
    limits = desktop_apu_limits(cpuinfo) if cpu == "amd" else None
    if cpu == "amd" and not limits and "Ryzen AI" in cpuinfo:
        # Strix / Krackan Point: AMD's configurable TDP goes to 54 W (HX 370);
        # handhelds with them top out around 35-40 W.
        limits = (5, 40, 20, 45)
    if limits:
        notes.append("Desktop APU: the TDP slider sets the socket power limit (PPT).")
    lo, hi, default, boost_max = limits or (5, 30, 15, 35)
    return Device(
        key="generic",
        name=_generic_name(vendor, board, cpuinfo),
        cpu=cpu,
        tdp_min=lo,
        tdp_max=hi,
        tdp_default=default,
        tdp_boost_max=boost_max,
        hwmon_names=("ayaneo_ec", "oxp_ec"),
        notes=notes,
        board=board,
        vendor=vendor,
    )
