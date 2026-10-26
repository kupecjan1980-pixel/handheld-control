"""Steam's performance overlay (mangoapp) left running outside games.

In Game Mode Steam starts mangoapp when the performance overlay is on. When
the game closes Steam hides the overlay but mangoapp keeps drawing it every
frame, so the GPU never idles on the Steam UI (ONEXPLAYER 3: mangoapp at
22 % of the GPU, GPU shown 100 %, ~11 W idle; 2026-10-26).

Opt-in: with no game running for GRACE seconds, the plugin ends mangoapp
(SIGTERM). Steam starts it again when a game shows the overlay. If Steam
brings it straight back (more than LOOP_MAX times in LOOP_WINDOW seconds),
the plugin stops trying and says so, rather than fighting it.
"""

import glob
import logging
import os
import signal
import time

log = logging.getLogger("hhctl")

NAME = "mangoapp"
GRACE = 10.0  # s with no game before it's ended (leaving a game, loading screens)
LOOP_WINDOW, LOOP_MAX = 120.0, 3


def pids() -> list[int]:
    out = []
    for path in glob.glob("/proc/[0-9]*/comm"):
        try:
            with open(path) as f:
                if f.read().strip() == NAME:
                    out.append(int(path.split("/")[2]))
        except (OSError, ValueError):
            continue
    return out


class IdleOverlay:
    def __init__(self):
        self.no_game_since: float | None = None
        self.last_look = 0.0
        self.stopped: list[float] = []  # when it was ended, for the loop check
        self.gave_up = False
        self.note: str | None = None  # for the panel
        self.seen_idle: str | None = None  # "running with no game since HH:MM", even when off

    def tick(self, enabled: bool, game_running: bool, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if game_running:
            self.no_game_since = None
            self.seen_idle = None
            return
        if self.no_game_since is None:
            self.no_game_since = now
        if now - self.no_game_since < GRACE or now - self.last_look < 5:
            return
        self.last_look = now
        running = pids()
        if not running:
            self.seen_idle = None
            return
        if not self.seen_idle:
            self.seen_idle = time.strftime("%H:%M")
            log.info("overlay: mangoapp runs with no game (pids %s)", running)
        if not enabled or self.gave_up:
            return
        self.stopped = [t for t in self.stopped if now - t < LOOP_WINDOW] + [now]
        if len(self.stopped) > LOOP_MAX:
            self.gave_up = True
            self.note = "Steam started it again right away each time, so HHC stopped ending it."
            log.info("overlay: %s", self.note)
            return
        for pid in running:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        self.note = f"Ended at {time.strftime('%H:%M')} (no game was running)."
        log.info("overlay: ended mangoapp %s", running)

    def reset(self) -> None:
        self.gave_up, self.stopped, self.note = False, [], None
