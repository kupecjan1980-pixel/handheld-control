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

# hibernated by Windows: ntfs-3g comes up read-only, so ntfs3 with force is kept
storage.shutil.which = lambda *a, **k: "/usr/bin/ntfs-3g"
storage._has_ntfs3 = lambda: True
import shutil as _sh; _sh.rmtree(f"{mnt}/Hry", ignore_errors=True)
ro = [True]
real_ro = storage._read_only
storage._read_only = lambda dev, w: ro.pop(0) if ro else False
calls.clear()
storage.mount("/dev/nvme1n1p2", home, force=True)
fst = open(storage.FSTAB).read()
assert f"{mnt}/Hry ntfs3 defaults,nofail,x-systemd.device-timeout=10s,force," in fst and "remove_hiberfile" not in fst, fst
assert ["umount", f"{mnt}/Hry"] in calls and storage.drives()[0]["forced"]
storage.mount("/dev/nvme1n1p2", home)  # option off: back to the plain line, same place
fst = open(storage.FSTAB).read()
assert f"{mnt}/Hry ntfs3 defaults,nofail" in fst and ",force" not in fst and not storage.drives()[0]["forced"], fst
storage.unmount("/dev/nvme1n1p2")
# never a Windows system drive
os.makedirs(f"{mnt}/Hry/Windows/System32")
real_dir = storage._mount_dir
storage._mount_dir = lambda d, keep=None: f"{mnt}/Hry"  # the drive's own files show up there
try:
    storage.mount("/dev/nvme1n1p2", home, force=True); raise SystemExit("Windows system drive must be refused")
except storage.StorageError as e:
    assert "Windows system drive" in str(e)
assert "AB12CD34" not in open(storage.FSTAB).read()
_sh.rmtree(f"{mnt}/Hry/Windows")
storage._mount_dir = real_dir
storage._read_only = real_ro

# someone else's fstab line: left alone
open(storage.FSTAB, "a").write("UUID=AB12CD34 /data ntfs3 defaults 0 0\n")
for fn in (lambda: storage.mount("/dev/nvme1n1p2", home), lambda: storage.unmount("/dev/nvme1n1p2"),
           lambda: storage.format_ext4("/dev/nvme1n1p2", home)):
    try:
        fn(); raise SystemExit("someone else's entry must be left alone")
    except storage.StorageError:
        pass
# an SD card the system mounted itself: can be made permanent, at the same place
tree["blockdevices"].append({"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "size": 128 << 30, "mountpoints": [None],
    "children": [{"name": "mmcblk0p1", "path": "/dev/mmcblk0p1", "type": "part", "fstype": "ntfs", "uuid": "SD1",
                  "size": 128 << 30, "mountpoints": ["/run/media/deck/SD"]}]})
sd = [x for x in storage.drives() if x["path"] == "/dev/mmcblk0p1"][0]
assert sd["auto"] and sd["can_mount"] and sd["model"] == "SD card" and "every start" in sd["note"]
storage.MOUNTS = f"{tmp}/mounts"
open(storage.MOUNTS, "w").write("/dev/mmcblk0p1 /run/media/deck/SD fuseblk rw,uid=1000 0 0\n")
real2 = storage.os.path.realpath
storage.os.path.realpath = lambda p: p if p.startswith("/dev/") else real2(p)
mk, ism = storage.os.makedirs, storage.os.path.ismount
storage.os.makedirs = lambda p, exist_ok=False: None if p.startswith("/run/media/") else mk(p, exist_ok=exist_ok)
storage.os.path.ismount = lambda p: False if p.startswith("/run/media/") else ism(p)
calls.clear()
try:
    storage.mount("/dev/mmcblk0p1", home)
except OSError:
    pass  # SteamLibrary folder under /run/media can't be made here
assert ["umount", "/run/media/deck/SD"] in calls, calls
assert "UUID=SD1 /run/media/deck/SD ntfs3 " in open(storage.FSTAB).read()
storage.unmount("/dev/mmcblk0p1")
assert "SD1" not in open(storage.FSTAB).read()
# an internal drive the desktop mounted: unmounted, and put back if our mount fails
tree["blockdevices"][-1]["name"] = "sdb"; tree["blockdevices"][-1]["tran"] = "sata"
storage.MOUNTS = f"{tmp}/mounts"
open(storage.MOUNTS, "w").write("/dev/mmcblk0p1 /run/media/deck/SD ntfs3 rw,uid=1000 0 0\n")
storage.os.path.realpath = lambda p: mnt if p == "/mnt" else p
tree["blockdevices"][-1]["children"][0]["mountpoints"] = ["/run/media/deck/SD"]
mount_ok[0] = False; calls.clear()
try:
    storage.mount("/dev/mmcblk0p1", home); raise SystemExit("should fail")
except storage.StorageError:
    pass
assert ["umount", "/run/media/deck/SD"] in calls
assert ["mount", "-t", "ntfs3", "-o", "rw,uid=1000", "/dev/mmcblk0p1", "/run/media/deck/SD"] in calls, calls
mount_ok[0] = True
tree["blockdevices"].pop()
storage.os.makedirs, storage.os.path.ismount = mk, ism

# an empty disk: partitioned, ext4, mounted for Steam
r = storage.format_ext4("/dev/sda", home)
assert ["sfdisk", "--label", "gpt", "/dev/sda"] in calls
assert ["mkfs.ext4", "-F", "-m", "0", "-L", "Games", "/dev/sda1"] in calls
assert r["mountpoint"] == f"{mnt}/Games" and "UUID=NEW1 " + f"{mnt}/Games ext4 defaults,noatime" in open(storage.FSTAB).read()
# every extra internal drive at once: USB drives left to the system, a Windows system drive left alone
open(storage.FSTAB, "w").write("UUID=sys / btrfs subvol=root 0 0\n")
tree["blockdevices"][2]["tran"] = "usb"
msg = storage.mount_all(home)
fst = open(storage.FSTAB).read()
assert "UUID=AB12CD34 " in fst and "NEW1" not in fst, fst
assert "Hry" in msg and "Mount at every start now" in msg, msg
assert "already" in storage.mount_all(home)
storage.unmount("/dev/nvme1n1p2")
os.makedirs(f"{mnt}/Hry/Windows/System32", exist_ok=True)
storage._mount_dir = lambda d, keep=None: f"{mnt}/Hry"
msg = storage.mount_all(home)
assert "Windows system drive" in msg and "AB12CD34" not in open(storage.FSTAB).read(), msg
storage._mount_dir = real_dir

# no password for the desktop's mounting: a polkit rule, on and off
storage.POLKIT_RULE = f"{tmp}/rules.d/49-hhc.rules"
assert not storage.nopass_state()["available"]
os.makedirs(f"{tmp}/rules.d")
storage.set_nopass(True)
assert storage.nopass_state() == {"available": True, "on": True}
assert "filesystem-mount-system" in open(storage.POLKIT_RULE).read() and "isInGroup(\"wheel\")" in open(storage.POLKIT_RULE).read()
storage.set_nopass(False)
assert not storage.nopass_state()["on"]
print("all storage tests passed")
