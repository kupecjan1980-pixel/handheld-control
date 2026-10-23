"""Updater: release list parsing, zip checks, folder swap, URL allow-list."""
import io, json, os, sys, tempfile, shutil, zipfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import updater

P = updater.DOWNLOAD_PREFIX
raw = [
    {"tag_name": "v0.10.5", "prerelease": True, "published_at": "2026-10-22T20:00:00Z", "body": "**Background** x",
     "assets": [{"name": "handheld-control-0.10.5.zip", "browser_download_url": P + "v0.10.5/handheld-control-0.10.5.zip"}]},
    {"tag_name": "v0.10.10", "prerelease": True, "published_at": "2026-10-23T00:00:00Z", "body": "",
     "assets": [{"name": "handheld-control-0.10.10.zip", "browser_download_url": P + "v0.10.10/handheld-control-0.10.10.zip"}]},
    {"tag_name": "v0.9.0", "draft": True, "assets": [{"name": "a.zip", "browser_download_url": P + "a.zip"}]},
    {"tag_name": "v0.8.0", "assets": []},
    {"tag_name": "v0.7.0", "assets": [{"name": "evil.zip", "browser_download_url": "https://evil.example/x.zip"}]},
]
rels = updater.parse_releases(raw)
assert [r["tag"] for r in rels] == ["v0.10.10", "v0.10.5"], "newest first by version, drafts / no zip / foreign URLs dropped"
assert rels[1]["date"] == "2026-10-22" and rels[1]["prerelease"]
print("release list ok")

def make_zip(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return b.getvalue()

R = "Handheld Control/"
good = {R + "plugin.json": json.dumps({"name": "Handheld Control"}), R + "main.py": "new", R + "dist/index.js": "js"}
tmp = tempfile.mkdtemp(); plugins = f"{tmp}/plugins"; pd = f"{plugins}/Handheld Control"
os.makedirs(pd); open(f"{pd}/main.py", "w").write("old"); open(f"{pd}/stale.py", "w").write("x")
updater.install_zip(make_zip(good), pd)
assert open(f"{pd}/main.py").read() == "new" and not os.path.exists(f"{pd}/stale.py")
assert os.listdir(plugins) == ["Handheld Control"], "no leftovers next to the plugin"
for bad, why in (
    ({**good, "Other/x.py": "x"}, "file outside the plugin folder"),
    ({**good, R + "../escape.py": "x"}, "path escape"),
    ({**good, R + "plugin.json": json.dumps({"name": "Something"})}, "another plugin"),
    ({k: v for k, v in good.items() if not k.endswith("index.js")}, "missing frontend"),
):
    try:
        updater.install_zip(make_zip(bad), pd); raise AssertionError(why)
    except OSError:
        pass
    assert open(f"{pd}/main.py").read() == "new", f"untouched after {why}"
assert os.listdir(plugins) == ["Handheld Control"]
try:
    updater.install("https://evil.example/handheld-control.zip", pd); raise AssertionError("foreign URL")
except OSError:
    pass
# the real release zip passes the checks
real = sorted(f for f in os.listdir(sys.argv[1] + "/out") if f.endswith(".zip")) if os.path.isdir(sys.argv[1] + "/out") else []
if real:
    updater.install_zip(open(f"{sys.argv[1]}/out/{real[-1]}", "rb").read(), pd)
    assert os.path.exists(f"{pd}/py_modules/hhctl/updater.py") or os.path.exists(f"{pd}/main.py")
assert [updater.version_key(v) for v in ("v0.10.10", "0.9")] == [(0, 10, 10), (0, 9)]
# desktop zips: listed and installed separately from the plugin zips
raw2 = [{"tag_name": "v0.11.0", "assets": [
    {"name": "handheld-control-0.11.0.zip", "browser_download_url": P + "v0.11.0/handheld-control-0.11.0.zip"},
    {"name": "handheld-control-desktop-0.11.0.zip", "browser_download_url": P + "v0.11.0/handheld-control-desktop-0.11.0.zip"}]}]
assert updater.parse_releases(raw2)[0]["url"].endswith("/handheld-control-0.11.0.zip")
assert updater.parse_releases(raw2, "desktop")[0]["url"].endswith("/handheld-control-desktop-0.11.0.zip")
assert updater.parse_releases(raw, "desktop") == [], "old releases have no desktop zip"
desk = {**good, R + "desktop/server.py": "s", R + "desktop/dist/bundle.js": "b"}
os.makedirs(pd, exist_ok=True)
try:
    updater.install_zip(make_zip(good), pd, "desktop"); raise AssertionError("plugin zip as desktop")
except OSError:
    pass
updater.install_zip(make_zip(desk), pd, "desktop"); assert open(f"{pd}/desktop/server.py").read() == "s"
try:
    updater.install(P + "v0.11.0/handheld-control-0.11.0.zip", pd, "desktop"); raise AssertionError("wrong kind URL")
except OSError:
    pass
print("desktop kind ok")
shutil.rmtree(tmp); print("all updater tests passed")
