"""Small helpers for reading and writing sysfs/procfs files."""

import glob
import os


def read(path: str, default: str | None = None) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(path: str, default: int | None = None) -> int | None:
    val = read(path)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def write(path: str, value) -> None:
    with open(path, "w") as f:
        f.write(f"{value}\n")


def writable(path: str) -> bool:
    return os.path.exists(path) and os.access(path, os.W_OK)


def choices(path: str) -> list[str]:
    """Parse sysfs choice lists like `[auto] inhibit-charge` or `a b c`."""
    val = read(path) or ""
    return val.replace("[", "").replace("]", "").split()


def selected(path: str) -> str | None:
    """Return the bracketed entry from a choice list, or the whole value."""
    val = read(path)
    if val is None:
        return None
    for tok in val.split():
        if tok.startswith("[") and tok.endswith("]"):
            return tok[1:-1]
    return val


def find_hwmon(names: tuple[str, ...]) -> str | None:
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if read(f"{d}/name") in names:
            return d
    return None
