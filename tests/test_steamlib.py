"""Adding a Steam library from outside Steam: into libraryfolders.vdf while
Steam is closed, pending (then written) while it's open."""
import os, sys, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import steamlib

tmp = tempfile.mkdtemp()
home = f"{tmp}/home"; root = f"{home}/.local/share/Steam"
os.makedirs(f"{root}/config"); os.makedirs(f"{root}/steamapps")
vdf = '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"/home/u/.local/share/Steam"\n\t\t"apps"\n\t\t{\n\t\t\t"228980"\t\t"1"\n\t\t}\n\t}\n}\n'
for f in ("config", "steamapps"):
    open(f"{root}/{f}/libraryfolders.vdf", "w").write(vdf)
lib = f"{tmp}/mnt/Hry/SteamLibrary"; os.makedirs(lib)
steamlib.PENDING = f"{tmp}/pending.json"
steamlib._add_via_cef = lambda p: False

running = [True]
steamlib.steam_running = lambda uid: running[0]
msg = steamlib.add(home, lib)
assert "close Steam" in msg and os.path.exists(steamlib.PENDING), msg
assert lib not in open(f"{root}/config/libraryfolders.vdf").read()
steamlib.tick()  # still open: waits
assert os.path.exists(steamlib.PENDING)
running[0] = False
steamlib.tick()
assert not os.path.exists(steamlib.PENDING)
for f in ("config", "steamapps"):
    t = open(f"{root}/{f}/libraryfolders.vdf").read()
    assert t.count(f'"path"\t\t"{lib}"') == 1 and '\t"1"\n\t{' in t and t.rstrip().endswith("}") and "228980" in t, t
assert os.path.isdir(f"{lib}/steamapps") and "contentid" in open(f"{lib}/libraryfolder.vdf").read()
assert "Added" in steamlib.add(home, lib)  # again: no second entry
assert open(f"{root}/config/libraryfolders.vdf").read().count(lib) == 1
print("all steamlib tests passed")
