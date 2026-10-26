"""Per-game profiles: TDP, EPP, CPU, GPU and fan settings kept per Steam game.

The panel reports the running game (Steam's MainRunningApp). When a game
with a profile starts, the settings in use are put aside as the "base" and
the profile's are applied; changes made while it runs go into the profile.
When it closes, the base comes back. The base is stored with the settings, so
a crash or restart mid-game still finds its way back.
"""

import copy

# Sections (and for the CPU, the keys) a profile holds. The rest (lights,
# battery, controller...) isn't about one game.
PROFILED: dict[str, tuple[str, ...] | None] = {
    "tdp": None,
    "epp": None,
    "gpu": None,
    "fan": None,
    "cpu": ("boost", "smt", "cores", "cap", "cap_mhz", "temp_limit", "ccx_off", "scx", "scx_mode"),
}
# "Leave it as the system set it" (None) can't undo what a profile changed:
# these put the system's usual state back instead.
NEUTRAL = {("cpu", "boost"): True, ("cpu", "smt"): True, ("cpu", "cores"): "all",
           ("cpu", "ccx_off"): False, ("cpu", "scx"): "off"}


def _keys(settings: dict, section: str) -> tuple[str, ...]:
    return PROFILED[section] or tuple(settings[section])


def snapshot(settings: dict) -> dict:
    return {sec: {k: copy.deepcopy(settings[sec].get(k)) for k in _keys(settings, sec)} for sec in PROFILED}


def put(settings: dict, snap: dict) -> None:
    """Apply a profile or the base onto the settings."""
    for sec, vals in snap.items():
        if sec not in PROFILED:
            continue
        for k, v in vals.items():
            if v is None and settings[sec].get(k) is not None and (sec, k) in NEUTRAL:
                v = NEUTRAL[(sec, k)]
            settings[sec][k] = copy.deepcopy(v)


def record(profile: dict, section: str, values: dict) -> None:
    """A change made while the game runs goes into its profile."""
    keys = PROFILED.get(section, ())
    if section not in PROFILED:
        return
    for k, v in values.items():
        if keys is None or k in keys:
            profile["settings"].setdefault(section, {})[k] = copy.deepcopy(v)
