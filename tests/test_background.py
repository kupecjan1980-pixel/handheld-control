"""Background picture: copy, replace, size/type limits, data URL, clear."""
import base64, json, os, sys, tempfile, shutil
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl.controller import Controller

tmp = tempfile.mkdtemp(); sdir = os.path.join(tmp, "settings")
png = os.path.join(tmp, "pic.png"); open(png, "wb").write(b"\x89PNG\r\n\x1a\nfake")
ctl = Controller(os.path.join(sdir, "s.json"), tmp, "0.8.1")
st = ctl.set_background(png)
name1 = st["settings"]["ui"]["bg"]; assert name1 and name1.endswith(".png")
url = ctl.background_data(); assert url.startswith("data:image/png;base64,")
assert base64.b64decode(url.split(",", 1)[1]) == open(png, "rb").read()
os.remove(png); assert ctl.background_data() == url, "works after the original is deleted"
png2 = os.path.join(tmp, "other.png"); open(png2, "wb").write(b"\x89PNG second")
name2 = ctl.set_background(png2)["settings"]["ui"]["bg"]
assert name2 != name1 and len([f for f in os.listdir(sdir) if f.startswith("background")]) == 1
print("copy, survives deleting the original, replacing gives a new name and one file")
bad = os.path.join(tmp, "x.gif"); open(bad, "wb").write(b"GIF")
st = ctl.set_background(bad); assert "unsupported" in st["errors"]["ui"] and st["settings"]["ui"]["bg"] == name2
big = os.path.join(tmp, "big.jpg"); open(big, "wb").write(b"\0" * (9 * 1024 * 1024))
st = ctl.set_background(big); assert "limit is 8 MB" in st["errors"]["ui"]
print("type and size limits refuse, keep the current picture")
st = ctl.clear_background(); assert st["settings"]["ui"]["bg"] is None and ctl.background_data() is None
assert not [f for f in os.listdir(sdir) if f.startswith("background")]
assert json.load(open(os.path.join(sdir, "s.json")))["ui"]["bg"] is None
print("clear removes the file and the setting")
shutil.rmtree(tmp); print("all background tests passed")
