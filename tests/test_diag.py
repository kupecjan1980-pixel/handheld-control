import sys, os, tempfile, shutil, threading, time, json
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import diag, backlight, sysfs
diag.SAMPLES = (0.05, 0.15)
tmp = tempfile.mkdtemp()
bl_dir = f"{tmp}/intel_backlight"; os.makedirs(bl_dir)
w = lambda k, v: open(f"{bl_dir}/{k}", "w").write(f"{v}\n")
for k, v in {"type": "raw", "brightness": 298, "actual_brightness": 298, "max_brightness": 472}.items(): w(k, v)
bl = backlight.Backlight(bl_dir)
# sticks (actual mirrors brightness via a follower thread)
stop = threading.Event()
def follower(revert=None):
    while not stop.is_set():
        b = sysfs.read(f"{bl_dir}/brightness")
        if revert and b != str(revert): time.sleep(0.02); w("brightness", revert)
        else: w("actual_brightness", b)
        time.sleep(0.01)
t = threading.Thread(target=follower); t.start()
v, lines = diag.brightness_test(bl); stop.set(); t.join()
assert v.startswith("The values stuck"), v
assert sysfs.read(f"{bl_dir}/brightness") == "298"   # restored
print("stuck:", v[:40], "| restored to 298")
# reverted by "gamescope"
stop.clear(); t = threading.Thread(target=follower, kwargs={"revert": 298}); t.start()
v, _ = diag.brightness_test(bl); stop.set(); t.join()
assert v.startswith("Something changed it back: wrote 118, it became 298"), v
print("reverted:", v[:60])
# driver doesn't apply (actual stays)
w("actual_brightness", 298)
v, _ = diag.brightness_test(bl)
assert v.startswith("The driver didn't apply it: wrote 118"), v
print("unapplied:", v[:50])
# write fails
bad = backlight.Backlight(bl_dir); bad.path = f"{tmp}/missing"
v, lines = diag.brightness_test(bad)
assert v.startswith("Writing the backlight failed"), v
print("write failure:", v[:45])
# report + controller flow: single pass, never touches PSR debugfs
home = f"{tmp}/home"; os.makedirs(home)
from hhctl import controller
controller.time.sleep = lambda s: None
ctl = controller.Controller(f"{tmp}/s.json", home)
ctl.backlight = bl
st = ctl.run_brightness_test(); assert st["display"]["test"]["phases"] == 1
while ctl.btest["running"]: time.sleep(0.05)
st = ctl.answer_brightness_test("neither")
rep = open(st["display"]["test"]["report"]).read()
assert "screen visibly changed in: neither" in rep and "[test]" in rep and "pass 2" not in rep
assert "[drm state]" in rep and st["display"]["test"]["report"].endswith("handheld-control-brightness-desktop.txt")
src = open(sys.argv[1] + "/py_modules/hhctl/controller.py").read() + open(sys.argv[1] + "/py_modules/hhctl/diag.py").read()
assert "sysfs.write(psr" not in src and "PSR_OFF =" not in src
json.dumps(st)
print("single-pass test, no PSR writes; report ok")
shutil.rmtree(tmp); print("all diag tests passed")
