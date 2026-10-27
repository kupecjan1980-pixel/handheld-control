"""OneXFly on Bazzite 44: InputPlumber gets a profile (Turbo -> F23, keyboard
button -> F24) and the Xbox Elite target; the listener reads its virtual
keyboard by name; everything goes back on restore."""
import json, os, sys, tempfile, struct, time, types
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import buttons, device, inputplumber as ip

tmp = tempfile.mkdtemp()
w = lambda p, v: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w").write(f"{v}\n"))

# --- fake InputPlumber on the bus -------------------------------------------
C0, C1 = "/org/shadowblip/InputPlumber/CompositeDevice0", "/org/shadowblip/InputPlumber/CompositeDevice1"
bus = {
    C0: {"Name": "ONEXPLAYER ONEXFLY", "ProfilePath": "/usr/share/inputplumber/profiles/default.yaml",
         "targets": ["deck-uhid", "mouse", "keyboard"]},
    C1: {"Name": "Sony DualSense", "ProfilePath": "", "targets": ["ds5"]},  # external pad: left alone
}
calls = []


def fake_run(args, timeout=10):
    out = ""
    if args[:2] == ["--list", "tree"]:
        out = "/\n/org/shadowblip/InputPlumber\n" + "\n".join(bus) + "\n/org/shadowblip/InputPlumber/devices/target/gamepad0\n"
    elif args[0] == "get-property":
        path, prop = args[2], args[4]
        if path.startswith("/t/"):  # target object: /t/<composite index>/<n>
            _, _, ci, n = path.split("/")
            out = json.dumps({"type": "s", "data": bus[list(bus)[int(ci)]]["targets"][int(n)]})
        elif prop == "TargetDevices":
            ci = list(bus).index(path)
            out = json.dumps({"type": "as", "data": [f"/t/{ci}/{n}" for n in range(len(bus[path]["targets"]))]})
        else:
            out = json.dumps({"type": "s", "data": bus[path][prop]})
    elif args[0] == "call":
        path, method = args[2], args[4]
        calls.append((path, method, args[6:]))
        if method == "LoadProfilePath":
            bus[path]["ProfilePath"] = args[6]
        elif method == "SetTargetDevices":
            bus[path]["targets"] = args[7:]
    return types.SimpleNamespace(returncode=0, stdout=out.encode(), stderr=b"")


ip._run = fake_run
ip.InputPlumber.RECHECK = 0.0  # check on every tick here
ip.InputPlumber.FAST = (0.0, 120.0)
ip.InputPlumber.FIGHT = (100, 300.0)
prof = f"{tmp}/inputplumber-profile.yaml"
p = ip.InputPlumber(prof)
p.tick(True, "xbox-elite")
assert p.devices == [C0], p.devices
assert bus[C0]["ProfilePath"] == prof and bus[C0]["targets"] == ["xbox-elite", "mouse", "keyboard"], bus[C0]
assert bus[C1]["targets"] == ["ds5"] and all(c[0] == C0 for c in calls)
data = json.load(open(prof))
m = {x["name"]: x for x in data["mapping"]}
assert m["HHC QuickAccess"]["target_events"] == [{"keyboard": "KeyF23"}]
assert m["HHC Keyboard"]["source_event"] == {"gamepad": {"button": "Keyboard"}}
assert "QuickAccess2" in m and "LeftStickDial clockwise" in m
n = len(calls)
p.tick(True, "xbox-elite")
assert len(calls) == n, "nothing to do when it already matches"
# InputPlumber makes the device anew after resume: re-applied, original still remembered
bus[C0].update(ProfilePath="/usr/share/inputplumber/profiles/default.yaml", targets=["deck-uhid", "mouse", "keyboard"])
p.tick(True, "xbox-elite")
assert bus[C0]["ProfilePath"] == prof and bus[C0]["targets"][0] == "xbox-elite"
# keyboard target added when missing (the keys need it)
bus[C0]["targets"] = ["deck-uhid"]
p.tick(True, "xbox-elite")
assert bus[C0]["targets"] == ["xbox-elite", "keyboard"], bus[C0]["targets"]
bus[C0]["targets"] = ["xbox-elite", "mouse", "keyboard"]
# buttons off: profile back; target "InputPlumber's choice": targets back
p.tick(False, "xbox-elite")
assert bus[C0]["ProfilePath"] == "/usr/share/inputplumber/profiles/default.yaml"
p.tick(False, None)
assert bus[C0]["targets"] == ["deck-uhid", "mouse", "keyboard"]
p.tick(True, "xb360")
p.restore()
assert bus[C0]["ProfilePath"] == "/usr/share/inputplumber/profiles/default.yaml"
assert bus[C0]["targets"] == ["deck-uhid", "mouse", "keyboard"]
assert not p.prev_profile and not p.prev_targets
# no InputPlumber: looked up once a minute, not every tick
q = ip.InputPlumber(prof)
ip._run = lambda args, timeout=10: (calls.append(args), types.SimpleNamespace(returncode=1, stdout=b"", stderr=b""))[1]
n = len(calls)
q.tick(True, "xbox-elite"); q.tick(True, "xbox-elite")
assert len(calls) == n + 1 and q.devices == []
# one switch by something else (Game Mode starting): put back, no giving up
ip._run = fake_run
ip.InputPlumber.FIGHT = (3, 300.0)
o = ip.InputPlumber(prof)
for _ in range(2):
    bus[C0].update(ProfilePath="/usr/share/inputplumber/profiles/default.yaml", targets=["deck-uhid"])
    assert o.tick(True, "xbox-elite") is True
assert not o.gave_up and len(o.sets) == 2, o.sets
assert o.tick(True, "xbox-elite") is False
# something else keeps changing it back: HHC stops instead of reconnecting the pad forever
ip._run = fake_run
ip.InputPlumber.FIGHT = (3, 300.0)
f = ip.InputPlumber(prof)
errors = []
for _ in range(6):
    bus[C0].update(ProfilePath="/usr/share/inputplumber/profiles/default.yaml", targets=["deck-uhid"])
    try:
        f.tick(True, "xbox-elite")
    except OSError as e:
        errors.append(str(e))
assert f.gave_up and errors and "keeps changing" in errors[0], (f.gave_up, errors)
n = len(calls)
f.tick(True, "xbox-elite")
assert len(calls) == n, "after giving up, InputPlumber is left alone"
# while it matches, checked every RECHECK seconds only
ip.InputPlumber.RECHECK = 30.0
g = ip.InputPlumber(prof)
g.fast_until = 0.0  # past the first checks after start
bus[C0].update(ProfilePath=prof, targets=["xbox-elite", "keyboard"])
g.tick(True, "xbox-elite"); n = len(calls); g.tick(True, "xbox-elite")
assert len(calls) == n, "no D-Bus calls between checks"

# --- listener on InputPlumber's virtual keyboard -----------------------------
gen = device.Device(**{**vars(device._next2("AYA", "AB09")), "key": "generic", "name": "ONEXPLAYER F1 EVA-02"})
assert buttons.map_key(gen) == "onexfly"
assert buttons.map_key(device.Device(**{**vars(gen), "name": "ASRock DeskMini X600"})) == "generic"
buttons.SYS_INPUT = f"{tmp}/sys"; buttons.DEV = f"{tmp}/dev"; os.makedirs(buttons.DEV)
bits = 1 << (193 % 64) | 1 << (194 % 64)
caps = f"{bits:x} 0 0 0"
for name, ev in (("Microsoft X-Box One Elite pad", "event3"), ("InputPlumber Keyboard", "event9")):
    d = f"{tmp}/sys/{ev}/device"
    w(f"{d}/name", name); w(f"{d}/id/vendor", "0000"); w(f"{d}/id/product", "0000"); w(f"{d}/capabilities/key", caps)
assert buttons.find_keyboard(None, "InputPlumber Keyboard", buttons.KEY_F23) == f"{tmp}/dev/event9"
node = f"{tmp}/dev/event9"; os.mkfifo(node)
got = []
b = buttons.Buttons(gen, got.append)
assert b.available() == (True, None) and "Turbo" in b.help
b.start()
wfd = os.open(node, os.O_WRONLY)
ev = lambda code, value: struct.pack("llHHi", 0, 0, 1, code, value) + struct.pack("llHHi", 0, 0, 0, 0, 0)
os.write(wfd, ev(193, 1) + ev(193, 0) + ev(194, 1) + ev(194, 0))
for _ in range(50):
    if len(got) >= 2:
        break
    time.sleep(0.05)
assert got == ["open_plugin", "qam"], got
b.stop(); os.close(wfd); b._thread.join(3)
# --- our device configs (NEXT 2, OXP3) --------------------------------------
sc = []
state = {"unit": True, "active": False}
def fake_systemctl(*args, timeout=30):
    sc.append(args)
    rc = 0
    if args[0] == "cat":
        rc = 0 if state["unit"] else 1
    elif args[0] == "is-active":
        rc = 0 if state["active"] else 3
    elif args[0] == "enable":
        state["active"] = True
    return types.SimpleNamespace(returncode=rc, stdout=b"", stderr=b"")
ip._systemctl = fake_systemctl
c = ip.Configs("ayaneo_next2", f"{tmp}/devices.d", f"{tmp}/maps.d")
assert c.supported() and not ip.Configs("onexfly").supported()
assert c.ensure() is True, "started: the caller checks the controller came up"
dev = json.load(open(c.dev_path)); cmap = json.load(open(c.map_path))
assert os.path.basename(c.dev_path) < "50-", "sorts before the stock configs"
assert dev["target_devices"][0] == "xbox-elite" and dev["capability_map_id"] == cmap["id"]
kb = dev["source_devices"][1]
assert kb["passthrough"] is True and kb["group"] == "keyboard", "the buttons' keyboard is never grabbed"
assert kb["unique"] is False, "both AYANEO interfaces join this one device (no second Elite pad)"
pad = {m["source_events"][0]["keyboard"]: m["target_event"]["gamepad"]["button"] for m in cmap["mapping"]}
assert pad == {"KeyF19": "LeftPaddle1", "KeyF17": "LeftPaddle2", "KeyF20": "RightPaddle1", "KeyF18": "RightPaddle2"}
assert ("enable", "--now", "inputplumber.service") in sc, sc
n = len(sc); c._status = None
assert c.ensure() is False
assert not any(a[0] in ("restart", "enable") for a in sc[n:]), "unchanged config: no restart"
open(c.dev_path, "w").write("old")
c._status = None; c.ensure()
assert sc[-1] == ("restart", "inputplumber.service")
c.remove()
assert not os.path.exists(c.dev_path) and not os.path.exists(c.map_path) and sc[-1][0] == "restart"
n = len(sc); c.remove()
assert len(sc) == n or sc[-1][0] != "restart", "nothing to remove: no restart"
o = ip.Configs("oxp3", f"{tmp}/devices.d", f"{tmp}/maps.d")
o.ensure()
od = json.load(open(o.dev_path))
assert not os.path.exists(o.map_path)
kbd = [x for x in od["source_devices"] if x["group"] == "keyboard"][0]
assert kbd["passthrough"] and kbd["events"] == {"exclude": ["*"]}
hid = [x for x in od["source_devices"] if "hidraw" in x][0]
assert "Gamepad:Button:Keyboard" in hid["events"]["exclude"]
o.remove()
state["unit"] = False
m = ip.Configs("oxp3", f"{tmp}/d2", f"{tmp}/m2")
assert m.status() == "missing"
m.ensure()
assert not os.path.exists(m.dev_path), "nothing written without InputPlumber"
# --- fail-safe health check: our composite, its sources, a gamepad target
def bus_for(name, sources, targets):
    def run(args, timeout=10):
        out = ""
        if args[:2] == ["--list", "tree"]:
            out = C0 + "\n"
        elif args[0] == "get-property":
            path, prop = args[2], args[4]
            if path.startswith("/t/"):
                out = json.dumps({"data": targets[int(path.split("/")[-1])]})
            elif prop == "Name":
                out = json.dumps({"data": name})
            elif prop == "SourceDevicePaths":
                out = json.dumps({"data": sources})
            elif prop == "TargetDevices":
                out = json.dumps({"data": [f"/t/0/{i}" for i in range(len(targets))]})
        return types.SimpleNamespace(returncode=0, stdout=out.encode(), stderr=b"")
    return run
h = ip.Configs("ayaneo_next2", f"{tmp}/d3", f"{tmp}/m3")
ip._run = bus_for("AYANEO NEXT 2 (HHC)", ["/s/0"], ["xbox-elite", "keyboard"])
assert h.health() is None
ip._run = bus_for("AYANEO NEXT 2 (HHC)", ["/s/0"], ["keyboard", "mouse"])
assert "no virtual controller" in h.health()
ip._run = bus_for("AYANEO NEXT 2 (HHC)", [], ["xbox-elite"])
assert "none of the controller" in h.health()
ip._run = bus_for("Something else", ["/s/0"], ["xbox-elite"])
assert "didn't take" in h.health()
print("all inputplumber tests passed")
