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
assert not buttons.Buttons(device._oxp3("ONE-NETBOOK", "ONEXPLAYER 3"), got.append).available()[0]
b.start()
wfd = os.open(node, os.O_WRONLY)  # blocks until the listener has it open
ev = lambda code, value: struct.pack("llHHi", 0, 0, 1, code, value) + struct.pack("llHHi", 0, 0, 0, 0, 0)
os.write(wfd, ev(193, 1) + ev(193, 0))  # AYN
os.write(wfd, ev(125, 1) + ev(32, 1) + ev(32, 0) + ev(125, 0))  # Win+D
os.write(wfd, ev(32, 1) + ev(32, 2) + ev(32, 0))  # plain D, with repeat: nothing
os.write(wfd, ev(187, 1) + ev(187, 0))  # F17 back button: not mapped yet
for _ in range(50):
    if len(got) >= 2:
        break
    time.sleep(0.05)
time.sleep(0.2)
assert got == ["open_plugin", "keyboard"], got
assert b.node == node
b.stop(); os.close(wfd); b._thread.join(3); assert not b._thread.is_alive()
print("find keyboard, AYN / Win+D actions, presses only, stop ok")
shutil.rmtree(tmp); print("all buttons tests passed")
