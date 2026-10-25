"""Extra buttons (NEXT 2): finds the AYANEO keyboard, AYN -> open_plugin, Meta+D -> keyboard."""
import os, sys, tempfile, shutil, struct, time
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import buttons, device

tmp = tempfile.mkdtemp()
w = lambda p, v: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w").write(f"{v}\n"))
# key bitmap from the NEXT 2 report (event8) has F23; event7 (the mouse) doesn't
assert buttons.has_key("7 ff80000000000000 e0b0ffdf01cfffff fffffffffffffffe", buttons.KEY_F23)
assert buttons.has_key("7 ff80000000000000 e0b0ffdf01cfffff fffffffffffffffe", buttons.KEY_D)
assert not buttons.has_key("70000 0 0 0 0", buttons.KEY_F23)
assert not buttons.has_key("0", 900)
buttons.SYS_INPUT = f"{tmp}/sys"; buttons.DEV = f"{tmp}/dev"; os.makedirs(buttons.DEV)
for n, (vid, pid, keys) in {"event7": ("1c4f", "0002", "70000 0 0 0 0"),
                            "event8": ("1c4f", "0002", "7 ff80000000000000 e0b0ffdf01cfffff fffffffffffffffe"),
                            "event10": ("045e", "028e", "7cdb000000000000 0 0 0 0")}.items():
    d = f"{tmp}/sys/{n}/device"
    w(f"{d}/id/vendor", vid); w(f"{d}/id/product", pid); w(f"{d}/capabilities/key", keys)
assert buttons.find_keyboard("1c4f", "0002", buttons.KEY_F23) == f"{tmp}/dev/event8"

node = f"{tmp}/dev/event8"; os.mkfifo(node)
got = []
next2 = device._next2("AYA", "AB09")
b = buttons.Buttons(next2, got.append)
assert b.available() == (True, None)
assert not buttons.Buttons(device.Device(**{**vars(next2), "key": "other"}), got.append).available()[0]
b.start()
wfd = os.open(node, os.O_WRONLY)  # blocks until the listener has it open
ev = lambda code, value: struct.pack("llHHi", 0, 0, 1, code, value) + struct.pack("llHHi", 0, 0, 0, 0, 0)
os.write(wfd, ev(193, 1) + ev(193, 0))  # AYN tap
os.write(wfd, ev(125, 1) + ev(32, 1) + ev(32, 0) + ev(125, 0))  # Win+D
os.write(wfd, ev(32, 1) + ev(32, 2) + ev(32, 0))  # plain D, with repeat: nothing
os.write(wfd, ev(187, 1) + ev(187, 0))  # F17 back button: not mapped yet
for _ in range(50):
    if len(got) >= 2:
        break
    time.sleep(0.05)
time.sleep(0.2)
assert got == ["open_plugin", "qam"], got
# held AYN: fires once on the press, nothing on repeat or release
os.write(wfd, ev(193, 1) + ev(193, 2)); time.sleep(buttons.HOLD + 0.25)
assert got[-1] == "open_plugin" and len(got) == 3, got
os.write(wfd, ev(193, 2) + ev(193, 0)); time.sleep(0.2)
assert len(got) == 3, got
os.write(wfd, ev(193, 1)); time.sleep(0.1); os.write(wfd, ev(193, 0)); time.sleep(0.2)
assert got[-1] == "open_plugin" and len(got) == 4, got
assert b.node == node
b.stop(); os.close(wfd); b._thread.join(3); assert not b._thread.is_alive()
print("find keyboard, AYN, Win+D, presses only, stop ok")

# --- ONEXPLAYER 3: Ctrl+Win+O keyboard button, Ctrl+Win+F17 home ---
oxp_caps = "1000000000007 ff800000000007ff febeffdfffefffff fefffffffffffffe"  # event5 in the OXP3 report
iface1 = "733eff 0 0 483ffff17aff32d bfd4444600000000 ffff0001 130ff38b17c000 677bfad941dfed 9ed68000004400 10000002"
assert buttons.has_key(oxp_caps, buttons.KEY_F17) and not buttons.has_key(iface1, buttons.KEY_F17)
for n, keys in {"event11": iface1, "event5": oxp_caps}.items():
    d = f"{tmp}/sys/{n}/device"
    w(f"{d}/id/vendor", "1a86"); w(f"{d}/id/product", "fe00"); w(f"{d}/capabilities/key", keys)
node = f"{tmp}/dev/event5"; os.mkfifo(node)
got.clear()
b = buttons.Buttons(device._oxp3("ONE-NETBOOK", "ONEXPLAYER 3"), got.append)
assert b.available() == (True, None)
b.start(); wfd = os.open(node, os.O_WRONLY)
combo = lambda key, held=0.0: (ev(29, 1) + ev(125, 1) + ev(key, 1), ev(key, 0) + ev(29, 0) + ev(125, 0), held)
for down, up, held in (combo(24), combo(187, 0.1), combo(187, buttons.HOLD + 0.25)):
    os.write(wfd, down); time.sleep(held); os.write(wfd, up); time.sleep(0.15)
time.sleep(0.2)
assert got == ["qam", "open_plugin", "open_plugin"], got
os.write(wfd, ev(187, 1) + ev(187, 0)); time.sleep(0.2)
assert len(got) == 3, "F17 without Win does nothing"
b.stop(); os.close(wfd); b._thread.join(3)
print("oxp3: Ctrl+Win+O Quick Access, Ctrl+Win+F17 Handheld Control ok")
shutil.rmtree(tmp); print("all buttons tests passed")
