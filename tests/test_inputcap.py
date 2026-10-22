"""Input recorder: device listing, event decoding, ABS/hid throttling, controller run."""
import json, os, sys, tempfile, shutil, threading, time
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import inputcap as ic

tmp = tempfile.mkdtemp()
w = lambda p, v: (os.makedirs(os.path.dirname(p), exist_ok=True), open(p, "w").write(f"{v}\n"))
ic.SYS_INPUT, ic.SYS_HIDRAW, ic.DEV = f"{tmp}/sys/input", f"{tmp}/sys/hidraw", f"{tmp}/dev"
d = f"{ic.SYS_INPUT}/event3/device"
w(f"{d}/name", "AT Translated Set 2 keyboard"); w(f"{d}/phys", "isa0060/serio0/input0")
for k, v in {"bustype": "0011", "vendor": "0001", "product": "0001", "version": "ab83"}.items():
    w(f"{d}/id/{k}", v)
w(f"{d}/capabilities/ev", "120013"); w(f"{d}/capabilities/key", "402000000 3803078f800d001")
h = f"{ic.SYS_HIDRAW}/hidraw0/device"
w(f"{h}/uevent", "DRIVER=hid-generic\nHID_ID=0003:00001C4F:00000002\nHID_NAME=SiGmaMicro Keyboard\nHID_PHYS=usb-1/input2")
open(f"{h}/report_descriptor", "wb").write(bytes([0x06, 0x00, 0xFF]))
os.makedirs(f"{ic.DEV}/input")
os.mkfifo(f"{ic.DEV}/input/event3"); os.mkfifo(f"{ic.DEV}/hidraw0")

devs = "\n".join(ic.devices())
assert "event3: 'AT Translated Set 2 keyboard' id=0011/0001/0001/ab83" in devs
assert "hidraw0: 'SiGmaMicro Keyboard' id=0003:00001C4F:00000002" in devs and "(3 bytes): 0600ff" in devs
print("devices: input + hidraw listing with ids, caps and report descriptor ok")

ev = lambda t, c, v: ic.EVENT.pack(0, 0, t, c, v)
def feed():
    time.sleep(0.3)
    with open(f"{ic.DEV}/input/event3", "wb", buffering=0) as f, open(f"{ic.DEV}/hidraw0", "wb", buffering=0) as g:
        # AYANEO-style extra button: Ctrl + Meta + F17 chord, then release
        f.write(ev(4, 4, 0x7C) + ev(1, 29, 1) + ev(1, 125, 1) + ev(1, 187, 1) + ev(0, 0, 0) + ev(1, 187, 0))
        f.write(b"".join(ev(3, 0x35, i) for i in range(50)))  # touchpad flood
        for rpt in (b"\x01\x00", b"\x01\x00", b"\x01\x08", b"\x01\x00"):
            g.write(rpt); time.sleep(0.05)
threading.Thread(target=feed, daemon=True).start()
ticks = []
lines, buttons = ic.record(1.5, ticks.append)
text = "\n".join(lines)
assert "event3 KEY 187 (F17) = 1" in text and "KEY 29 (LEFTCTRL) = 1" in text and "MSC 4 = 124" in text
assert "SYN" not in text and "EV 0" not in text
assert text.count("ABS 53 (MT_POSITION_X)") == ic.ABS_PER_CODE + 1, "throttled + summary line"
assert "sent 50 events, first 30 shown" in text
assert [l.split()[-1] for l in lines if " hid " in l] == ["0100", "0108", "0100"], "repeated reports skipped"
assert buttons == {"event3": 4} and ticks and ticks[0] == 2
print("record: key chord + MSC decoded, SYN skipped, ABS throttled, hid deduped, progress ok")

# controller: runs in a thread and writes the report to the user's home
from hhctl import controller
home = f"{tmp}/home"; os.makedirs(home)
ctl = controller.Controller(f"{tmp}/s/s.json", home)
ic.record = lambda s, p: (p(1), (["0.1 event3 KEY 187 (F17) = 1"], {"event3": 2}))[1]
st = ctl.run_input_capture(1)
for _ in range(50):
    if not ctl.icap["running"]:
        break
    time.sleep(0.05)
st = ctl.state()["input_capture"]; json.dumps(st)
assert not st["running"] and st["summary"] == "Button presses seen on event3 (2)", st
rep = open(st["report"]).read()
assert st["report"].startswith(home) and "[events]" in rep and "F17" in rep and "[devices]" in rep
shutil.rmtree(tmp); print("all inputcap tests passed")
