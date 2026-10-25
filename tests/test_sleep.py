"""Lights off on PrepareForSleep (delay inhibitor let go after it, taken again
on wake); USB wakeup enabled along the gamepad's path and put back."""
import json, os, sys, tempfile, types
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import sleep

# --- PrepareForSleep handling
spawned, killed = [], []
class FakeProc:
    def __init__(self, cmd, **kw): self.cmd, self.alive = cmd, True; spawned.append(self)
    def poll(self): return None if self.alive else 0
    def terminate(self): self.alive = False; killed.append(self.cmd[0])
    def wait(self, *a): return 0
    def kill(self): self.alive = False
sleep.proc.popen = FakeProc
calls = []
w = sleep.SleepWatch(lambda: calls.append("off"))
w._take()
assert spawned[-1].cmd[:3] == ["systemd-inhibit", "--what=sleep", "--mode=delay"]
sig = lambda going: json.dumps({"type": "signal", "member": "PrepareForSleep", "payload": {"type": "b", "data": [going]}})
w.handle(sig(True))
assert calls == ["off"] and killed == ["systemd-inhibit"], (calls, killed)
assert w._inhibit is None, "let go so the suspend goes on"
w.handle(sig(False))
assert w._inhibit is spawned[-1] and spawned[-1].cmd[0] == "systemd-inhibit", "taken again after wake"
w.handle("not json"); w.handle(json.dumps({"member": "Other"}))
assert calls == ["off"]
# a failing light-off still lets the suspend go on
w2 = sleep.SleepWatch(lambda: 1 / 0); w2._take(); w2.handle(sig(True)); assert w2._inhibit is None

# --- wakeup along the USB path
tmp = tempfile.mkdtemp()
sleep.SYS_INPUT = f"{tmp}/class/input"
pci = f"{tmp}/devices/pci0000:00/0000:00:08.1/0000:c4:00.3"
usb = f"{pci}/usb1/1-4"
inp = f"{usb}/1-4:1.0/input/input10"  # the input device (id/vendor, id/product)
for d in (pci, f"{pci}/usb1", usb):
    os.makedirs(f"{d}/power", exist_ok=True)
    open(f"{d}/power/wakeup", "w").write("disabled\n")
open(f"{usb}/idVendor", "w").write("045e\n")
open(f"{pci}/usb1/idVendor", "w").write("1d6b\n")
os.makedirs(f"{inp}/id")
for k, v in (("vendor", "045e"), ("product", "028e")):
    open(f"{inp}/id/{k}", "w").write(v + "\n")
os.makedirs(f"{inp}/event10")
os.symlink(inp, f"{inp}/event10/device")
os.makedirs(sleep.SYS_INPUT)
os.symlink(f"{inp}/event10", f"{sleep.SYS_INPUT}/event10")
p = sleep.PadWake([("045e", "028e")])
files = p.files()
assert files == [f"{usb}/power/wakeup", f"{pci}/usb1/power/wakeup", f"{pci}/power/wakeup"], files
p.enable()
assert all(open(f).read().strip() == "enabled" for f in files)
p.restore()
assert all(open(f).read().strip() == "disabled" for f in files)
assert not sleep.PadWake([("dead", "beef")]).available()
# wakeup_count tells which device woke it
open(f"{usb}/power/wakeup_count", "w").write("3\n")
p.snapshot()
assert p.woke_by() == []
open(f"{usb}/power/wakeup_count", "w").write("4\n")
assert p.woke_by() == ["045e:028e"]

# controller: woken right away by a device -> lights back, that device off the list
from hhctl import controller, device
controller.detect = lambda: device._next2("AYA", "AB09")
ctl = controller.Controller(os.path.join(tmp, "s.json"))
applied = []
ctl.apply_rgb = lambda: applied.append("rgb")
ctl.settings["sleep"]["pad_wake"] = True
ctl.padwake = sleep.PadWake([("045e", "028e")])
ctl.padwake.enable()
ctl._before_sleep()
open(f"{usb}/power/wakeup_count", "w").write("9\n")
ctl._after_wake()
ctl.tick_wake()
assert applied == ["rgb"], "lights back after every wake"
assert ctl.padwake.ids == [] and "045e:028e" in ctl.settings["sleep"]["pad_wake_skip"]
assert open(f"{usb}/power/wakeup").read().strip() == "disabled", "its wakeup taken back"
assert "woke it right away" in ctl.state()["sleep"]["note"]
# a normal (long) sleep woken by a device: kept
ctl.padwake = sleep.PadWake([("045e", "028e")]); ctl.padwake.enable()
ctl._before_sleep(); ctl.slept_at -= 3600
open(f"{usb}/power/wakeup_count", "w").write("10\n")
ctl._after_wake(); ctl.tick_wake()
assert ctl.padwake.ids == [("045e", "028e")]
assert controller.PAD_IDS["ayaneo_next2"] == [("1c4f", "0002")], "NEXT 2: buttons only"
# naming the wake source from /sys/class/wakeup
wk = f"{tmp}/wakeup"
for i, (name, wc, ec) in enumerate([("1-4", 0, 2), ("PNP0C0C:00", 0, 0), ("0000:c4:00.3", 0, 5)]):
    os.makedirs(f"{wk}/wakeup{i}")
    for k, v in (("name", name), ("wakeup_count", wc), ("event_count", ec)):
        open(f"{wk}/wakeup{i}/{k}", "w").write(f"{v}\n")
sleep.WAKEUP_CLASS = wk
before = sleep.wake_counts()
open(f"{wk}/wakeup0/event_count", "w").write("3\n")
assert [x.split()[0] for x in sleep.woken_by(before, sleep.wake_counts())] == ["1-4"]
open(f"{wk}/wakeup1/wakeup_count", "w").write("1\n")
assert sleep.woken_by(before, sleep.wake_counts()) == ["PNP0C0C:00"], "wakeup_count wins over event_count"
print("all sleep tests passed")
