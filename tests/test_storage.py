"""Extra drives: the system disk is never listed; mounting writes a tagged
fstab entry (NTFS without windows_names) and a failed mount takes it back."""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[1] + "/py_modules")
from hhctl import storage

tmp = tempfile.mkdtemp()
storage.FSTAB = f"{tmp}/fstab"
open(storage.FSTAB, "w").write("UUID=sys / btrfs subvol=root 0 0\n")
mnt = f"{tmp}/mnt"; os.makedirs(mnt)
real = os.path.realpath
storage.os.path.realpath = lambda p: mnt if p == "/mnt" else real(p)
home = f"{tmp}/home"; os.makedirs(home)

tree = {"blockdevices": [
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "type": "disk", "size": 1 << 40, "model": "SYS", "mountpoints": [None],
     "children": [
         {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "type": "part", "fstype": "vfat", "uuid": "E", "parttypename": "EFI System", "mountpoints": ["/boot/efi"], "size": 1 << 29},
         {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "type": "part", "fstype": "btrfs", "uuid": "S", "mountpoints": ["/sysroot", "/var"], "size": 1 << 39}]},
    {"name": "nvme1n1", "path": "/dev/nvme1n1", "type": "disk", "size": 2 << 40, "model": "WD_BLACK", "mountpoints": [None],
     "children": [
         {"name": "nvme1n1p1", "path": "/dev/nvme1n1p1", "type": "part", "fstype": None, "parttypename": "Microsoft reserved", "size": 16 << 20},
         {"name": "nvme1n1p2", "path": "/dev/nvme1n1p2", "type": "part", "fstype": "ntfs", "label": "Hry", "uuid": "AB12CD34", "size": 2 << 40, "mountpoints": [None]}]},
    {"name": "sda", "path": "/dev/sda", "type": "disk", "size": 1 << 39, "model": "USB SSD", "fstype": None, "mountpoints": [None]},
    {"name": "zram0", "path": "/dev/zram0", "type": "disk", "size": 1 << 33, "mountpoints": ["[SWAP]"]},
]}
calls, mount_ok = [], [True]
def run(cmd, **kw):
    calls.append(cmd)
    out, rc, err = "", 0, ""
    if cmd[0] == "lsblk" and "-J" in cmd:
        out = json.dumps(tree)
    elif cmd[0] == "mount":
        rc, err = (0, "") if mount_ok[0] else (32, "wrong fs type, bad superblock")
        if rc == 0:
            os.makedirs(f"{cmd[1]}/lost", exist_ok=True); os.rmdir(f"{cmd[1]}/lost")
    elif cmd[:2] == ["lsblk", "-nro"] and cmd[2] == "PATH,TYPE":
        out = "/dev/sda disk\n/dev/sda1 part\n"
    elif cmd[0] == "mkfs.ext4":
        tree["blockdevices"][2]["children"] = [{"name": "sda1", "path": cmd[-1], "type": "part", "fstype": "ext4",
                                                "label": cmd[cmd.index("-L") + 1], "uuid": "NEW1", "size": 1 << 39, "mountpoints": [None]}]
    return subprocess.CompletedProcess(cmd, rc, out, err)
storage.proc.run = run
storage.os.path.ismount = lambda p: False
storage._ntfs_type = lambda: "ntfs3"
storage.os.chown = lambda *a: None

ds = storage.drives()
paths = [d["path"] for d in ds]
assert paths == ["/dev/nvme1n1p2", "/dev/sda"], paths
ntfs, empty = ds
assert ntfs["can_mount"] and "NTFS" in ntfs["note"] and not ntfs["permanent"]
assert empty["empty"] and not empty["can_mount"]
try:
    storage.mount("/dev/nvme0n1p2", home); raise SystemExit("system disk must be refused")
except storage.StorageError:
    pass

r = storage.mount("/dev/nvme1n1p2", home)
assert r["mountpoint"] == f"{mnt}/Hry" and r["library"] == f"{mnt}/Hry/SteamLibrary" and os.path.isdir(r["library"])
fst = open(storage.FSTAB).read()
assert "UUID=AB12CD34 " + f"{mnt}/Hry ntfs3 " in fst and "windows_names" not in fst and "nofail" in fst, fst
assert fst.startswith("UUID=sys /"), "the old lines stay"
assert os.path.exists(storage.FSTAB + ".hhc.bak")
d = storage.drives()[0]
assert d["permanent"] and d["ours"]
storage.mount("/dev/nvme1n1p2", home)  # again: no second line
assert open(storage.FSTAB).read().count("AB12CD34") == 1
storage.unmount("/dev/nvme1n1p2")
assert "AB12CD34" not in open(storage.FSTAB).read() and "UUID=sys" in open(storage.FSTAB).read()

# a failed mount takes its entry back out
mount_ok[0] = False
try:
    storage.mount("/dev/nvme1n1p2", home); raise SystemExit("should fail")
except storage.StorageError as e:
    assert "Fast Startup" in str(e)
assert "AB12CD34" not in open(storage.FSTAB).read()
mount_ok[0] = True

# someone else's fstab line: left alone
open(storage.FSTAB, "a").write("UUID=AB12CD34 /data ntfs3 defaults 0 0\n")
for fn in (lambda: storage.mount("/dev/nvme1n1p2", home), lambda: storage.unmount("/dev/nvme1n1p2"),
           lambda: storage.format_ext4("/dev/nvme1n1p2", home)):
    try:
        fn(); raise SystemExit("someone else's entry must be left alone")
    except storage.StorageError:
        pass
# an empty disk: partitioned, ext4, mounted for Steam
r = storage.format_ext4("/dev/sda", home)
assert ["sfdisk", "--label", "gpt", "/dev/sda"] in calls
assert ["mkfs.ext4", "-F", "-m", "0", "-L", "Games", "/dev/sda1"] in calls
assert r["mountpoint"] == f"{mnt}/Games" and "UUID=NEW1 " + f"{mnt}/Games ext4 defaults,noatime" in open(storage.FSTAB).read()
print("all storage tests passed")
