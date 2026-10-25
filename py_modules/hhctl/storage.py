"""Extra drives for a Steam library: mounted from /etc/fstab at boot, so no
password prompt (udisks asks for one, and can't mount NTFS by itself).

The system disk is never listed: a disk holding /, /boot, /sysroot, /var,
/home or swap, or anything encrypted or LVM, is left out whole. Our fstab
entries carry a comment line above them; only those are ever removed. A
mount that fails takes its entry back out, so a bad line can't hold up boot
(nofail is set anyway). /etc/fstab is copied once to /etc/fstab.hhc.bak.
"""

import json
import logging
import os
import re
import shutil

from . import proc

log = logging.getLogger("hhctl")

FSTAB = "/etc/fstab"
TAG = "# Handheld Control:"
SYSTEM_MOUNTS = {"/", "/boot", "/boot/efi", "/efi", "/sysroot", "/usr", "/etc", "/var", "/home", "/var/home"}
HIDDEN_FS = {"swap", "crypto_LUKS", "LVM2_member", "linux_raid_member", "zfs_member", "bcache"}
HIDDEN_PARTS = ("efi system", "microsoft reserved", "windows recovery", "bios boot")
MOUNTABLE = {"ext4", "btrfs", "xfs", "ntfs", "exfat", "vfat"}
NO_PROTON = {"exfat", "vfat"}  # no symlinks or permissions: Proton prefixes don't work there


class StorageError(OSError):
    pass


def _lsblk() -> list[dict]:
    r = proc.run(["lsblk", "-J", "-b", "-o",
                  "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,TRAN,PARTTYPENAME"],
                 text=True, timeout=10)
    if r.returncode:
        raise StorageError(f"lsblk failed: {r.stderr.strip()}")
    return json.loads(r.stdout).get("blockdevices", [])


def _walk(node: dict):
    yield node
    for c in node.get("children") or []:
        yield from _walk(c)


def _mounts(node: dict) -> list[str]:
    return [m for m in node.get("mountpoints") or [] if m]


def _is_system(disk: dict) -> bool:
    for n in _walk(disk):
        if n.get("fstype") in HIDDEN_FS or n.get("type") in ("crypt", "lvm"):
            return True
        if any(m in SYSTEM_MOUNTS or m == "[SWAP]" for m in _mounts(n)):
            return True
    return False


# --- /etc/fstab ---------------------------------------------------------------------

def _fstab_lines() -> list[str]:
    try:
        with open(FSTAB) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def _write_fstab(lines: list[str]) -> None:
    bak = FSTAB + ".hhc.bak"
    if os.path.exists(FSTAB) and not os.path.exists(bak):
        shutil.copy2(FSTAB, bak)
    tmp = FSTAB + ".hhc.tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")
    if os.path.exists(FSTAB):
        shutil.copymode(FSTAB, tmp)
    os.replace(tmp, FSTAB)


def _ours() -> dict[str, str]:
    """UUID -> mount point of our entries."""
    out, lines = {}, _fstab_lines()
    for i, line in enumerate(lines[:-1]):
        if line.startswith(TAG):
            f = lines[i + 1].split()
            if len(f) >= 2 and f[0].startswith("UUID="):
                out[f[0][5:]] = f[1]
    return out


def _listed(uuid: str) -> bool:
    """In fstab at all (ours or someone else's), by UUID."""
    for line in _fstab_lines():
        f = line.split()
        if f and not f[0].startswith("#") and f[0] in (f"UUID={uuid}", f"/dev/disk/by-uuid/{uuid}"):
            return True
    return False


def _remove_entry(uuid: str) -> None:
    lines, out, skip = _fstab_lines(), [], False
    for i, line in enumerate(lines):
        if skip:
            skip = False
            continue
        nxt = lines[i + 1].split() if i + 1 < len(lines) else []
        if line.startswith(TAG) and nxt and nxt[0] == f"UUID={uuid}":
            skip = True
            continue
        out.append(line)
    _write_fstab(out)


def _reload() -> None:
    proc.run(["systemctl", "daemon-reload"], timeout=30)


# --- drives ---------------------------------------------------------------------

def drives() -> list[dict]:
    """Volumes on the non-system disks, plus empty disks (format only)."""
    ours = _ours()
    out = []
    for disk in _lsblk():
        if disk.get("type") != "disk" or _is_system(disk):
            continue
        removable = disk.get("name", "").startswith("mmcblk") or disk.get("tran") in ("usb", "mmc")
        model = (disk.get("model") or ("SD card" if disk.get("name", "").startswith("mmcblk") else None)
                 or disk.get("tran") or "Drive").strip()
        parts = [c for c in disk.get("children") or [] if c.get("type") == "part"]
        vols = parts or [disk]
        if not parts and not disk.get("fstype"):
            out.append(_entry(disk, model, ours, removable, empty=True))
            continue
        for v in vols:
            pt = (v.get("parttypename") or "").lower()
            if any(pt.startswith(h) for h in HIDDEN_PARTS) or v.get("fstype") in HIDDEN_FS:
                continue
            if not v.get("fstype") and int(v.get("size") or 0) < 1 << 30:
                continue  # small unformatted gap, e.g. Microsoft reserved without a type name
            out.append(_entry(v, model, ours, removable))
    return out


def _entry(v: dict, model: str, ours: dict, removable: bool = False, empty: bool = False) -> dict:
    fs, uuid = v.get("fstype"), v.get("uuid")
    mounts = _mounts(v)
    listed = bool(uuid) and _listed(uuid)
    # An SD card or USB drive the system mounted by itself (Bazzite/SteamOS
    # automount it when inserted, no password): left where Steam knows it.
    auto = removable and bool(mounts) and not listed
    note = None
    if auto:
        note = "The system mounts it by itself when inserted, no password needed"
    elif empty:
        note = "Empty drive: format it to use it"
    elif not fs:
        note = "Not formatted"
    elif fs not in MOUNTABLE:
        note = f"{fs} isn't supported here"
    elif fs == "ntfs":
        note = "NTFS: Proton games often break on it; ext4 is better if Windows doesn't need this drive"
    elif fs in NO_PROTON:
        note = f"{fs}: Windows games (Proton) won't run from it; native Linux games will"
    return {
        "path": v["path"],
        "model": model,
        "size": int(v.get("size") or 0),
        "fstype": fs,
        "label": v.get("label"),
        "uuid": uuid,
        "mountpoint": mounts[0] if mounts else None,
        "permanent": listed,
        "ours": bool(uuid) and uuid in ours,
        "can_mount": bool(uuid) and fs in MOUNTABLE and not auto,
        "auto": auto,
        "empty": empty,
        "note": note,
    }


def _find(path: str) -> dict:
    for d in drives():
        if d["path"] == path:
            return d
    raise StorageError(f"{path} isn't one of the extra drives (the system drive is never touched)")


# --- mounting -------------------------------------------------------------------

def _owner(home: str) -> tuple[int, int]:
    st = os.stat(home)
    return st.st_uid, st.st_gid


def _mount_dir(d: dict) -> str:
    base = os.path.realpath("/mnt")  # /var/mnt on Bazzite and other image-based systems
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", d["label"] or "").strip("-.") or f"drive-{d['uuid'][:8]}"
    path = os.path.join(base, name)
    n = 2
    while os.path.exists(path) and (os.path.ismount(path) or os.listdir(path)):
        path = os.path.join(base, f"{name}-{n}")
        n += 1
    return path


def _ntfs_type() -> str:
    proc.run(["modprobe", "ntfs3"], timeout=10)
    try:
        with open("/proc/filesystems") as f:
            if "ntfs3" in f.read().split():
                return "ntfs3"
    except OSError:
        pass
    if shutil.which("ntfs-3g", path=proc.SYSTEM_PATH):
        return "ntfs-3g"
    raise StorageError("No NTFS driver: the kernel has no ntfs3 and ntfs-3g isn't installed")


def _fstab_line(d: dict, where: str, uid: int, gid: int) -> str:
    fs = d["fstype"]
    common = "nofail,x-systemd.device-timeout=10s"
    if fs == "ntfs":
        kind = _ntfs_type()
        # No windows_names: it refuses names like "c:", and every Proton prefix has them.
        return f"UUID={d['uuid']} {where} {kind} defaults,{common},uid={uid},gid={gid},umask=022 0 0"
    if fs in NO_PROTON:
        return f"UUID={d['uuid']} {where} {fs} defaults,{common},uid={uid},gid={gid},umask=022 0 0"
    return f"UUID={d['uuid']} {where} {fs} defaults,noatime,{common} 0 {2 if fs == 'ext4' else 0}"


MOUNTS = "/proc/self/mounts"


def _current_mounts(dev: str) -> list[tuple[str, str, str]]:
    """(target, fstype, options) of each mount of this device."""
    out = []
    real = os.path.realpath(dev)
    try:
        with open(MOUNTS) as f:
            for line in f:
                p = line.split()
                if len(p) >= 4 and os.path.realpath(p[0]) == real:
                    out.append((p[1].replace("\\040", " "), p[2], p[3]))
    except OSError:
        pass
    return out


def _unmount_all(d: dict) -> list[tuple[str, str, str]]:
    """Unmount it everywhere; returns what was mounted, for _remount()."""
    was = _current_mounts(d["path"])
    for target, _, _ in was:
        u = proc.run(["umount", target], text=True, timeout=60)
        if u.returncode:
            _remount(d["path"], was)
            raise StorageError(f"Can't unmount {target}: {u.stderr.strip() or 'in use'}. "
                               "Close what uses it (Steam downloads, a file manager) and try again.")
    return was


def _remount(dev: str, was: list[tuple[str, str, str]]) -> None:
    """Put back mounts we took away, best effort (FUSE ones go through ntfs-3g)."""
    for target, fstype, opts in was:
        if os.path.ismount(target):
            continue
        kind = "ntfs-3g" if fstype.startswith("fuse") else fstype
        try:
            os.makedirs(target, exist_ok=True)  # udisks removes its folder on unmount
        except OSError as e:
            log.warning("storage: no %s (%s)", target, e)
        r = proc.run(["mount", "-t", kind, "-o", opts, dev, target], text=True, timeout=60)
        if r.returncode:
            log.warning("storage: couldn't put %s back at %s: %s", dev, target, r.stderr.strip())


def _steam_dir(where: str, uid: int, gid: int) -> str:
    lib = os.path.join(where, "SteamLibrary")
    os.makedirs(lib, exist_ok=True)
    if os.stat(lib).st_uid == 0:
        os.chown(lib, uid, gid)
    return lib


def mount(path: str, home: str) -> dict:
    """Mount now and at every boot. Returns {"mountpoint", "library"}."""
    d = _find(path)
    if not d["can_mount"]:
        raise StorageError(d["note"] or "This drive can't be mounted")
    if d["permanent"] and not d["ours"]:
        raise StorageError("This drive already has its own line in /etc/fstab; change it there")
    uid, gid = _owner(home)
    was = []
    if d["ours"]:
        where = _ours()[d["uuid"]]
    else:
        was = _unmount_all(d)  # e.g. mounted by the desktop under /run/media
        where = _mount_dir(d)
        line = _fstab_line(d, where, uid, gid)
        _write_fstab(_fstab_lines() + [f"{TAG} {d['model']} {d['path']}", line])
        log.info("storage: fstab + %s", line)
    os.makedirs(where, exist_ok=True)
    _reload()
    if not os.path.ismount(where):
        r = proc.run(["mount", where], text=True, timeout=60)
        if r.returncode:
            _remove_entry(d["uuid"])
            _reload()
            _remount(d["path"], was)
            msg = (r.stderr or "").strip() or f"mount exited with {r.returncode}"
            if d["fstype"] == "ntfs":
                k = proc.run(["dmesg"], text=True, timeout=10)
                if "dirty" in "\n".join((k.stdout or "").splitlines()[-30:]):
                    msg += (". Windows didn't close it cleanly (it was pulled out or Windows hibernated): "
                            "eject it properly in Windows, or run chkdsk there, then try again")
                else:
                    msg += (". If Windows uses this drive, turn off Fast Startup there and shut Windows down "
                            "fully (or run chkdsk), then try again")
            raise StorageError(f"Couldn't mount it, nothing was changed: {msg}")
    if d["fstype"] in ("ext4", "btrfs", "xfs") and os.stat(where).st_uid == 0 \
            and set(os.listdir(where)) <= {"lost+found"}:
        os.chown(where, uid, gid)  # a fresh filesystem: the user's, so Steam can write to it
    return {"mountpoint": where, "library": _steam_dir(where, uid, gid)}


def unmount(path: str) -> None:
    """Unmount and take our fstab entry out (the data stays)."""
    d = _find(path)
    if d["permanent"] and not d["ours"]:
        raise StorageError("This drive's line in /etc/fstab isn't ours; change it there")
    _unmount_all(d)
    if d["ours"]:
        where = _ours()[d["uuid"]]
        _remove_entry(d["uuid"])
        _reload()
        try:
            os.rmdir(where)  # only if empty
        except OSError:
            pass


def _settle() -> None:
    proc.run(["udevadm", "settle", "--timeout=10"], timeout=20)


def format_ext4(path: str, home: str) -> dict:
    """Erase it, make one ext4 filesystem and mount it for Steam."""
    d = _find(path)
    if d["permanent"] and not d["ours"]:
        raise StorageError("This drive has its own line in /etc/fstab; remove that first")
    _unmount_all(d)
    if d["ours"]:
        _remove_entry(d["uuid"])
        _reload()
    target = d["path"]
    if d["empty"]:
        r = proc.run(["sfdisk", "--label", "gpt", target], input="label: gpt\n,,L\n", text=True, timeout=60)
        if r.returncode:
            raise StorageError(f"Couldn't partition it: {r.stderr.strip()}")
        _settle()
        kids = proc.run(["lsblk", "-nro", "PATH,TYPE", target], text=True, timeout=10).stdout.split("\n")
        parts = [k.split()[0] for k in kids if k.endswith(" part")]
        if not parts:
            raise StorageError("The new partition didn't show up; try again")
        target = parts[0]
    label = re.sub(r"[^A-Za-z0-9_-]", "", d["label"] or "")[:16] or "Games"
    proc.run(["wipefs", "-a", target], timeout=60)
    r = proc.run(["mkfs.ext4", "-F", "-m", "0", "-L", label, target], text=True, timeout=600)
    if r.returncode:
        raise StorageError(f"Formatting failed: {(r.stderr or '').strip()[-300:]}")
    _settle()
    return mount(target, home)
