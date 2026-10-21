"""Device detection and per-device hardware descriptions.

Register maps come from the upstream kernel drivers:
- AYANEO NEXT 2 (board AB09, vendor "AYA"): same EC layout as ayaneo-ec's
  quirk_charge_limit devices (fan 0x4A/0x4B, tach 0x76, charge 0x1E).
- ONEXPLAYER 3: mapped to oxp_g1_i in oxpec (pdx86 for-next).
"""

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
        ec_charge=EcCharge(behaviour_reg=0x1E, auto=0xAA, inhibit=0x55),
        rgb="ayaneo_hid",
        # From a NEXT 2 EC map: ports 0x62/0x66 mirror this window. Only
        # used after it matches port reads register for register.
        ec_mmio=0xFE800400,
        notes=[
            "No EC charge-% register: the limit is enforced by toggling "
            "charge inhibit while the plugin runs.",
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
        board=board,
        vendor=vendor,
    )


def detect() -> Device:
    vendor = sysfs.read(f"{DMI}/board_vendor") or sysfs.read(f"{DMI}/sys_vendor") or ""
    board = sysfs.read(f"{DMI}/board_name") or ""
    if vendor in ("AYA", "AYANEO") and board == "AB09":
        return _next2(vendor, board)
    if vendor == "ONE-NETBOOK" and board == "ONEXPLAYER 3":
        return _oxp3(vendor, board)

    # Unknown device: only use kernel interfaces, never raw EC writes.
    cpu = "intel" if "GenuineIntel" in (sysfs.read("/proc/cpuinfo") or "") else "amd"
    return Device(
        key="generic",
        name=f"{vendor} {board}".strip() or "Unknown device",
        cpu=cpu,
        tdp_min=5,
        tdp_max=30,
        tdp_default=15,
        tdp_boost_max=35,
        hwmon_names=("ayaneo_ec", "oxp_ec"),
        notes=["Unsupported device: EC fallbacks are disabled."],
        board=board,
        vendor=vendor,
    )
