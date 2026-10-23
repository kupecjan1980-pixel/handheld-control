import sys, os, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import rgb, device
tmp = tempfile.mkdtemp(); node = f"{tmp}/hidraw9"
sent = []
class Rec(rgb.OxpHidV2):
    def send(self, pkts, reply_timeout=0.0):
        sent.extend(pkts)
d = Rec(node)
d.apply("solid", 0, 100, 100, None)
assert all(len(p) == 64 for p in sent)
assert sent[0][:6] == bytes([0x07, 0xFF, 0xFD, 0x01, 0x05, 0x04]), sent[0][:6].hex()
assert sent[1][:3] == bytes([0x07, 0xFF, 0xFE]) and sent[1][3:6] == bytes([255, 0, 0]) and sent[1][3:63] == bytes([255, 0, 0]) * 20 and sent[1][63] == 0
sent.clear(); d.apply("effect", 0, 0, 20, "aurora")
assert sent[0][3:6] == bytes([0x01, 0x05, 0x01]) and sent[1][:3] == bytes([0x07, 0xFF, 0x01])
sent.clear(); d.apply("off", 0, 0, 50, None)
assert len(sent) == 1 and sent[0][3] == 0x00
# matches HHD's gen_cmd byte for byte
def gen_cmd(cid, cmd, size=64):
    base = bytes([cid, 0xFF, *bytes(cmd)]); return base + bytes([0] * (size - len(base)))
assert rgb._oxp_v2_cmd(0x07, [0xFD, 1, 0x05, 0x03]) == gen_cmd(0x07, [0xFD, 1, 0x05, 0x03])
# generic devices pick it up when the OneXFly interface is there
real = rgb.find_hidraw
rgb.find_hidraw = lambda vid, pid, page, usage=1: node if (vid, pid, page) == (0x1A2C, 0xB001, 0xFF01) else None
dev = device.detect(); dev.rgb = None
assert isinstance(rgb.find_backend(dev), rgb.OxpHidV2)
rgb.find_hidraw = real
shutil.rmtree(tmp)
print("all oxp v2 rgb tests passed")
