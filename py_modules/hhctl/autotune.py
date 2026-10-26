"""Auto-tune a game: share the power between the CPU and GPU by what holds it back.

On an APU the CPU and GPU share one power limit; every watt the CPU doesn't
need can go to the GPU. While a game with auto-tune runs, the GPU's load
and the busiest CPU core's are averaged over WINDOW seconds, and the CPU
clock limit moves a step at a time:

- GPU-bound (GPU busy, no CPU core maxed out): the CPU gives way, so the
  GPU gets the power (or, below the power limit, it just runs cooler).
- CPU-bound (a core maxed out while the GPU waits): the CPU gets it back.
- Light load (frame cap, menus): the CPU gives way to save power.

Giving way: the clock limit first (never below FLOOR of the top clock), then
SMT off. What it settles on is kept in the game's profile, so the next start
begins there.

Never "small cores only": that limits all of user.slice, Steam included, and
on the ONEXPLAYER 3 (4 LP E-cores) quitting a game then took so long it
looked stuck; the forced reboot left the SD card's library unreadable
(2026-10-26).
"""

WINDOW = 20.0  # seconds averaged per decision
STEP = 200  # MHz
FLOOR = 0.5  # of the top clock

GPU_BOUND, CPU_BUSY = 90, 85
GPU_WAITING, CORE_MAXED = 80, 90
LIGHT = 55


class AutoTune:
    def __init__(self):
        self.samples: list[tuple[float, float]] = []  # (gpu %, busiest core %)
        self.started: float | None = None
        self.status: str | None = None  # last decision, for the panel

    def reset(self) -> None:
        self.samples, self.started, self.status = [], None, None

    def feed(self, now: float, gpu: float | None, core: float | None) -> None:
        if gpu is None or core is None:
            return
        if self.started is None:
            self.started = now
        self.samples.append((gpu, core))

    def decide(self, now: float, cpu: dict, lo: int, hi: int, can_smt: bool) -> dict | None:
        """After a full window: the CPU settings to change (a dict for
        settings["cpu"]), or None to leave them. cpu: settings["cpu"] now.

        Giving way goes a step at a time: the clock limit down to FLOOR, then
        SMT off (half the threads). A maxed-out core undoes it in the
        opposite order; cores left on the small ones (older versions) come
        back first."""
        if self.started is None or now - self.started < WINDOW or len(self.samples) < 5:
            return None
        gpu = sum(g for g, _ in self.samples) / len(self.samples)
        core = sum(c for _, c in self.samples) / len(self.samples)
        self.samples, self.started = [], None
        floor = max(lo, int(hi * FLOOR) // 100 * 100)
        cur = int(cpu["cap_mhz"]) if cpu.get("cap") and cpu.get("cap_mhz") else hi
        light = gpu < LIGHT and core < LIGHT
        change: dict = {}
        if gpu >= GPU_BOUND and core < CPU_BUSY or light:
            why = "light load" if light else "GPU-bound"
            if cur > floor:
                new = max(floor, cur - STEP)
                change = {"cap": True, "cap_mhz": new}
                what = f"CPU limit {new} MHz"
            elif can_smt and cpu.get("smt") is not False:
                change, what = {"smt": False}, "SMT off"
            else:
                what = "as low as it goes"
        elif core >= CORE_MAXED and gpu < GPU_WAITING:
            why = "CPU-bound"
            if cpu.get("cores") == "small":
                change, what = {"cores": "all"}, "all cores"
            elif cpu.get("smt") is False:
                change, what = {"smt": True}, "SMT on"
            elif cur < hi:
                new = min(hi, cur + 2 * STEP)
                change = {"cap": False} if new >= hi else {"cap": True, "cap_mhz": new}
                what = "no CPU limit" if new >= hi else f"CPU limit {new} MHz"
            else:
                what = "already at full speed"
        else:
            why, what = "balanced", "no change"
        self.status = f"{why} (GPU {gpu:.0f} %, busiest core {core:.0f} %): {what}"
        return change or None
