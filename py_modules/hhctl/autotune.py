"""Auto-tune a game: no power spent where the game doesn't need it.

On an APU the CPU and GPU share one power limit. While a game with auto-tune
runs, the GPU's load and the busiest CPU core's are averaged over WINDOW
seconds, and one setting moves a step at a time:

- Light load (frame cap, menus, simple games): save battery. The CPU clock
  limit comes down to FLOOR of the top clock, then CPU boost goes off, then
  the energy preference (EPP) goes to balance_power, then the TDP comes down
  a watt at a time to TDP_FLOOR of where it started.
- GPU-bound (GPU busy, no CPU core maxed out): the GPU needs the power. A
  TDP lowered earlier goes back up first; then the CPU gives way (clock
  limit, boost off) so the shared power goes to the GPU.
- CPU-bound (a core maxed out while the GPU waits): undo in reverse order:
  TDP back, EPP back, boost back on, clock limit up.

Never cores or SMT: taking threads away slowed loading and quitting games
(SMT off on the OneXFly, small cores only on the ONEXPLAYER 3; 2026-10-26).
What it settles on is kept in the game's profile, so the next start begins
there; when the game closes the usual settings come back.
"""

WINDOW = 20.0  # seconds averaged per decision
STEP = 200  # MHz
FLOOR = 0.5  # of the top clock
TDP_STEP, TDP_UP = 1, 2  # W per step down / up
TDP_FLOOR = 0.6  # of the TDP it started from

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

    def decide(self, now: float, s: dict, can: dict) -> dict | None:
        """After a full window: {section: {key: value}} to change, or None.

        s: the settings now ("cpu", "tdp", "epp"). can: what this device
        offers: lo / hi (CPU clock range, MHz; None without a clock limit),
        boost (bool), epp (bool), tdp (start W or None), tdp_min (W)."""
        if self.started is None or now - self.started < WINDOW or len(self.samples) < 5:
            return None
        gpu = sum(g for g, _ in self.samples) / len(self.samples)
        core = sum(c for _, c in self.samples) / len(self.samples)
        self.samples, self.started = [], None
        cpu, tdp, epp = s["cpu"], s["tdp"], s["epp"]
        hi, lo = can.get("hi"), can.get("lo") or 0
        floor = max(lo, int(hi * FLOOR) // 100 * 100) if hi else 0
        cur = int(cpu["cap_mhz"]) if hi and cpu.get("cap") and cpu.get("cap_mhz") else hi
        start = can.get("tdp")
        watts = int(tdp["watts"]) if start and tdp.get("enabled") and tdp.get("watts") else start
        tdp_floor = max(can.get("tdp_min") or 0, round(start * TDP_FLOOR)) if start else 0
        epp_low = bool(can.get("epp") and epp.get("enabled") and epp.get("value") == "balance_power")

        def cap_down():
            new = max(floor, cur - STEP)
            return {"cpu": {"cap": True, "cap_mhz": new}}, f"CPU limit {new} MHz"

        light = gpu < LIGHT and core < LIGHT
        gpu_bound = gpu >= GPU_BOUND and core < CPU_BUSY
        change, what = None, None
        if gpu_bound:
            why = "GPU-bound"
            if start and watts < start:
                new = min(start, watts + TDP_UP)
                change, what = {"tdp": {"enabled": True, "watts": new}}, f"TDP {new} W"
            elif hi and cur > floor:
                change, what = cap_down()
            elif can.get("boost") and cpu.get("boost") is not False:
                change, what = {"cpu": {"boost": False}}, "CPU boost off"
        elif light:
            why = "light load"
            if hi and cur > floor:
                change, what = cap_down()
            elif can.get("boost") and cpu.get("boost") is not False:
                change, what = {"cpu": {"boost": False}}, "CPU boost off"
            elif can.get("epp") and not epp_low:
                change, what = {"epp": {"enabled": True, "value": "balance_power"}}, "EPP balance_power"
            elif start and watts > tdp_floor:
                new = max(tdp_floor, watts - TDP_STEP)
                change, what = {"tdp": {"enabled": True, "watts": new}}, f"TDP {new} W"
        elif core >= CORE_MAXED and gpu < GPU_WAITING:
            why = "CPU-bound"
            if start and watts < start:
                new = min(start, watts + TDP_UP)
                change, what = {"tdp": {"enabled": True, "watts": new}}, f"TDP {new} W"
            elif epp_low:
                change, what = {"epp": {"value": "balance_performance"}}, "EPP balance_performance"
            elif can.get("boost") and cpu.get("boost") is False:
                change, what = {"cpu": {"boost": True}}, "CPU boost on"
            elif hi and cur < hi:
                new = min(hi, cur + 2 * STEP)
                change = {"cpu": {"cap": False} if new >= hi else {"cap": True, "cap_mhz": new}}
                what = "no CPU limit" if new >= hi else f"CPU limit {new} MHz"
            else:
                what = "already at full speed"
        else:
            why, what = "balanced", "no change"
        if what is None:
            what = "as low as it goes"
        self.status = f"{why} (GPU {gpu:.0f} %, busiest core {core:.0f} %): {what}"
        return change
