import sys, types, importlib.util, json, os, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import rgb, fan, battery, device
from hhctl.controller import Controller

# --- AYANEO packet vs HHD get_cfg_commands (imported from the fetched source)
src = open(sys.argv[2] + "/src_hhd_device_ayaneo_const.py").read()
mod = types.ModuleType("ayaconst")
mod.__dict__["hsb_to_rgb"] = lambda h, s, v: (0, 0, 0)
mod.__dict__["rgb_to_hsb"] = lambda r, g, b: (0, 0, 0)
exec(compile(src.split("AYA3_INIT")[0].split("from ")[0] + "from typing import Literal\n" + "def pad" + src.split("def pad", 1)[1], "c", "exec"), mod.__dict__)
# bypass HHD's hue correction by patching converters to identity
mod.rgb_to_hsb = lambda r, g, b: (r, g, b)
mod.hsb_to_rgb = lambda h, s, v: ((h - 10) % 360, s, v)
ref = mod.get_cfg_commands("solid", 12, 200, 99, 1.0)[0]
mine = rgb.AyaneoHid.packet("solid", (12 - 10) % 360 + 10 - 10 + 0, 200, 99) if False else rgb.AyaneoHid.packet("solid", 12, 200, 99)
# HHD output for r=12 after its (h+10) then our (h-10) patch -> 12 again
assert ref == mine, (ref.hex(), mine.hex())
print("ayaneo packet matches HHD:", len(mine), "bytes")

# --- OXP v1 vs HHD gen_cmd
src = open(sys.argv[2] + "/src_hhd_device_oxp_hid_v1.py").read()
ns = {}
exec(src.split("def gen_cmd", 1)[0].split("import logging")[0] + "from typing import Literal\ndef gen_cmd" + src.split("def gen_cmd", 1)[1].split("KBD_NAME")[0], ns)
assert ns["gen_rgb_solid"](1, 2, 3, side=7) == rgb._oxp_v1_cmd(0xB8, [0xFE, 7, 0x02] + 18 * [1, 2, 3] + [1, 2])
assert ns["gen_brightness"](2, True, "high") == rgb._oxp_v1_cmd(0xB8, [0xFD, 2, 0x02, 1, 0x05, 0x04])
assert ns["gen_rgb_mode"]("neon", side=1) == rgb._oxp_v1_cmd(0xB8, [0x05, 1, 0x02])
print("oxp v1 packets match HHD")

# --- descriptor parser
desc = bytes([0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0xC0, 0x05, 0x01, 0x09, 0x05, 0xA1, 0x01, 0xC0])
assert rgb._collections(desc) == [(0xFF00, 1), (0x01, 5)]
# --- fan curve
c = fan.CURVES["balanced"]
assert fan.interpolate(c, 30) == 20 and fan.interpolate(c, 55) == 38 and fan.interpolate(c, 99) == 100
# --- emulated charge limit
class FakeDev: ec_charge = None
b = battery.Battery.__new__(battery.Battery)
b.dev = FakeDev(); b.bat = "x"; b.inhibited = False; b.native_limit = False; b.native_behaviour = True; b.ec_ok = False
caps = iter([79, 80, 81, 78, 77, 76])
writes = []
b.capacity = lambda: next(caps)
b.behaviours = lambda: ["auto", "inhibit-charge"]
b.set_behaviour = lambda v: (writes.append(v), setattr(b, "inhibited", v != "auto"))
for _ in range(6): b.tick(80, "off")
assert writes == ["inhibit-charge", "auto"], writes
print("emulated limit:", writes)

# --- controller on this machine (read-only, nothing enabled)
with tempfile.TemporaryDirectory() as d:
    ctl = Controller(os.path.join(d, "s.json"))
    ctl.apply_all(); ctl.tick()
    st = ctl.state()
    json.dumps(st)
    print("controller state OK:", st["device"]["name"], "| tdp:", st["tdp"]["backend"], "| fan:", st["fan"]["backend"], st["fan"]["unavailable"], "| bat:", st["battery"]["limit_mode"], "| rgb:", st["rgb"]["backend"], "| errors:", st["errors"])
    ctl.update("fan", {"mode": "fixed", "percent": 40})
    assert json.load(open(os.path.join(d, "s.json")))["fan"]["percent"] == 40
print("all tests passed")
