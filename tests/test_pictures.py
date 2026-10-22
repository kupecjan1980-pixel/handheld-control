"""Picture browser: folder listing, desktop thumbnail cache, size limit, bg position reset."""
import hashlib, os, sys, tempfile, shutil, time, urllib.parse
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import background, controller

tmp = tempfile.mkdtemp(); home = f"{tmp}/home"; pics = f"{home}/Pictures"
os.makedirs(f"{pics}/Trips"); os.makedirs(f"{pics}/.hidden")
w = lambda p, data: open(p, "wb").write(data)
w(f"{pics}/old.png", b"\x89PNG old"); time.sleep(0.01)
w(f"{pics}/new.jpg", b"\xff\xd8 new"); w(f"{pics}/notes.txt", b"x"); w(f"{pics}/.secret.png", b"x")
w(f"{pics}/huge.webp", b"0" * (background.MAX_BYTES + 1))
ld = background.list_dir(pics, home)
assert ld["dirs"] == ["Trips"] and ld["parent"] == home
names = [f["name"] for f in ld["files"]]
assert set(names) == {"old.png", "new.jpg", "huge.webp"} and names.index("new.jpg") < names.index("old.png"), names
# no cache: the picture itself; too big: None
assert background.thumb(f"{pics}/new.jpg", home).startswith("data:image/jpeg;base64,")
assert background.thumb(f"{pics}/huge.webp", home) is None
# fresh desktop thumbnail wins, even for a picture too big to send
uri = "file://" + urllib.parse.quote(os.path.realpath(f"{pics}/huge.webp"))
t = f"{home}/.cache/thumbnails/large/{hashlib.md5(uri.encode()).hexdigest()}.png"
os.makedirs(os.path.dirname(t)); w(t, b"\x89PNG thumb")
assert background.thumb(f"{pics}/huge.webp", home) == "data:image/png;base64," + __import__("base64").b64encode(b"\x89PNG thumb").decode()
os.utime(t, (1, 1)); assert background.thumb(f"{pics}/huge.webp", home) is None, "stale cache ignored"
print("list_dir and thumb ok")

ctl = controller.Controller(os.path.join(tmp, "s", "s.json")); ctl.user_home = home
assert ctl.list_pictures(None)["folder"] == os.path.realpath(pics), "starts in ~/Pictures"
assert "error" in ctl.list_pictures(f"{tmp}/nope")
assert ctl.settings["ui"]["bg_pos"] == {"x": 50, "y": 50, "zoom": 100}
ctl.update("ui", {"bg_pos": {"x": 10, "y": 80, "zoom": 200}})
ctl.set_background(f"{pics}/new.jpg"); assert ctl.settings["ui"]["bg_pos"] == {"x": 50, "y": 50, "zoom": 100}, "new picture recentered"
assert ctl.picture_thumb(f"{tmp}/missing.png") is None
shutil.rmtree(tmp); print("all picture tests passed")
