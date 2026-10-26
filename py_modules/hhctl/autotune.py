"""Auto-tune a game: share the power between the CPU and GPU by what holds it back.

On an APU the CPU and GPU share one power limit; every watt the CPU doesn't
need can go to the GPU. While a game with auto-tune runs, the GPU's load
and the busiest CPU core's are averaged over WINDOW seconds, and the CPU
clock limit moves a step at a time:

- GPU-bound (GPU busy, no CPU core maxed out): lower the CPU limit, so the
  GPU gets the power (or, below the power limit, it just runs cooler).
- CPU-bound (a core maxed out while the GPU waits): raise it again.
- Light load (frame cap, menus): lower it to save power.

It never goes below FLOOR of the CPU's top clock, and the limit it settles
on is kept in the game's profile, so the next start begins there.
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

    def decide(self, now: float, cap: int | None, lo: int, hi: int) -> int | None | bool:
        """After a full window: the new CPU limit (MHz), None for no limit,
        or False to leave it. cap: the limit now (None: none)."""
        if self.started is None or now - self.started < WINDOW or len(self.samples) < 5:
            return False
        gpu = sum(g for g, _ in self.samples) / len(self.samples)
        core = sum(c for _, c in self.samples) / len(self.samples)
        self.samples, self.started = [], None
        floor = max(lo, int(hi * FLOOR) // 100 * 100)
        cur = cap or hi
        if gpu >= GPU_BOUND and core < CPU_BUSY:
            why, new = "GPU-bound", max(floor, cur - STEP)
        elif core >= CORE_MAXED and gpu < GPU_WAITING:
            why, new = "CPU-bound", min(hi, cur + 2 * STEP)
        elif gpu < LIGHT and core < LIGHT:
            why, new = "light load", max(floor, cur - STEP)
        else:
            why, new = "balanced", cur
        self.status = f"{why} (GPU {gpu:.0f} %, busiest core {core:.0f} %)"
        if new == cur:
            return False
        return None if new >= hi else new
