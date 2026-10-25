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
print("all inputplumber tests passed")
