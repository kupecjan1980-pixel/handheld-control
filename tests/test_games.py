import sys, copy
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import games, controller
C = controller.Controller
ctl = C.__new__(C)
ctl.settings = copy.deepcopy(controller.DEFAULTS)
ctl.game = ctl.pre_game = None
applied, tdp_applied = [], []
ctl.save = lambda: None
ctl.state = lambda: {}
ctl.apply_profiled = lambda: applied.append(copy.deepcopy(ctl.settings))
for n in ("tdp", "epp", "cpu", "gpu", "fan", "games"):
    setattr(ctl, f"apply_{n}", (lambda n: lambda *a, **k: None)(n))
ctl.apply_games = lambda: C.apply_games(ctl)
class T:
    def apply(self, dev, w, boost): tdp_applied.append(w)
class D: tdp_default = 25
ctl.tdp, ctl.dev = T(), D()
ctl._guard = lambda sec, fn: fn()
ctl.errors, ctl.sleep_note, ctl.fan, ctl.fan_why = {}, None, None, None
s = ctl.settings

# A game without a profile: nothing changes; tuning it, then saving keeps the usual settings as the base.
C.set_game(ctl, "100", "Doom")
assert not applied
C.update(ctl, "tdp", {"enabled": True, "watts": 12})
C.update(ctl, "cpu", {"smt": False})
C.save_game_profile(ctl, True)
assert s["games"]["profiles"]["100"]["settings"]["tdp"]["watts"] == 12
assert s["games"]["base"]["tdp"]["enabled"] is False, "the base is what ran before the game"
# A change while it runs goes into the profile.
C.update(ctl, "tdp", {"watts": 14})
assert s["games"]["profiles"]["100"]["settings"]["tdp"]["watts"] == 14
# Game closes: the usual settings, SMT back on, the default TDP once.
C.set_game(ctl, None)
assert s["tdp"]["enabled"] is False and s["cpu"]["smt"] is True and s["games"]["base"] is None
assert tdp_applied == [25], tdp_applied
C.update(ctl, "tdp", {"watts": 20})
assert s["games"]["profiles"]["100"]["settings"]["tdp"]["watts"] == 14, "no game: the profile is left alone"
# Starts again: its profile.
C.set_game(ctl, "100", "Doom")
assert s["tdp"] == {"enabled": True, "watts": 14, "boost": True} and s["cpu"]["smt"] is False
# Straight to another game with no profile: usual settings.
C.set_game(ctl, "200", "Other")
assert s["tdp"]["enabled"] is False and s["tdp"]["watts"] == 20
# Profiles off while a profiled game runs: usual settings; on again: the profile.
C.set_game(ctl, "100", "Doom")
C.update(ctl, "games", {"enabled": False})
assert s["tdp"]["enabled"] is False
C.update(ctl, "games", {"enabled": True})
assert s["tdp"]["watts"] == 14
# Profile removed while it runs: usual settings.
C.save_game_profile(ctl, False)
assert "100" not in s["games"]["profiles"] and s["tdp"]["enabled"] is False and s["games"]["base"] is None
print("all game profile tests passed")
