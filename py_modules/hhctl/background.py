"""Custom background picture for the plugin's panel.

The chosen image is copied into the plugin's settings folder, so the panel
keeps working if the original is moved or deleted. The frontend can't load
local files directly, so it gets the picture once as a data URL.

The panel's own picture browser lists a folder (list_dir) and shows each
picture as a thumbnail (thumb): the desktop's thumbnail cache when there is
one, else the picture itself, which the panel shrinks.
"""

import base64
import glob
import hashlib
import os
import shutil
import time
import urllib.parse

MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
MAX_BYTES = 8 * 1024 * 1024  # it travels to the panel as base64
# Bigger pictures go to the panel once, get shrunk there, and come back.
MAX_SOURCE = 40 * 1024 * 1024


MAX_LISTED = 300


def list_dir(folder: str, home: str) -> dict:
    """Subfolders and pictures in `folder` (hidden entries skipped)."""
    folder = os.path.realpath(folder)
    dirs, files = [], []
    with os.scandir(folder) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                if e.is_dir():
                    dirs.append(e.name)
                elif e.is_file() and os.path.splitext(e.name)[1].lower() in MIME:
                    files.append({"name": e.name, "path": e.path, "size": e.stat().st_size, "mtime": e.stat().st_mtime})
            except OSError:
                continue
    dirs.sort(key=str.lower)
    files.sort(key=lambda f: -f["mtime"])  # newest first, like a gallery
    parent = os.path.dirname(folder)
    return {
        "folder": folder,
        "parent": parent if parent != folder else None,
        "home": home,
        "dirs": dirs,
        "files": files[:MAX_LISTED],
        "more": max(0, len(files) - MAX_LISTED),
    }


def _cached_thumb(path: str, home: str) -> str | None:
    # freedesktop thumbnail spec: md5 of the file URI, PNG in ~/.cache/thumbnails.
    uri = "file://" + urllib.parse.quote(os.path.realpath(path))
    name = hashlib.md5(uri.encode()).hexdigest() + ".png"
    mtime = os.path.getmtime(path)
    for size in ("large", "x-large", "normal"):
        t = os.path.join(home, ".cache", "thumbnails", size, name)
        if os.path.exists(t) and os.path.getmtime(t) >= mtime:
            return t
    return None


def thumb(path: str, home: str) -> str | None:
    """A data URL for a thumbnail of `path`, or None if it's too big to send."""
    src = _cached_thumb(path, home)
    if not src:
        if os.path.splitext(path)[1].lower() not in MIME or os.path.getsize(path) > MAX_SOURCE:
            return None
        src = path
    mime = MIME.get(os.path.splitext(src)[1].lower(), "image/png")
    with open(src, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


def read_data_url(path: str) -> str:
    """A picture of up to MAX_SOURCE as a data URL, for the panel to shrink."""
    mime = MIME.get(os.path.splitext(path)[1].lower())
    if not mime:
        raise OSError("unsupported image type (use PNG, JPG or WebP)")
    size = os.path.getsize(path)
    if size > MAX_SOURCE:
        raise OSError(f"image is {size // (1024 * 1024)} MB; the limit is {MAX_SOURCE // (1024 * 1024)} MB")
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


def save_data_url(folder: str, data_url: str) -> str:
    """Store a picture the panel sent back (already shrunk) as the background."""
    head, _, b64 = data_url.partition(",")
    ext = next((e for e, m in MIME.items() if head == f"data:{m};base64"), None)
    if not ext:
        raise ValueError("not a PNG, JPG or WebP data URL")
    data = base64.b64decode(b64, validate=True)
    if len(data) > MAX_BYTES:
        raise OSError(f"picture is still {len(data) // (1024 * 1024)} MB after shrinking")
    os.makedirs(folder, exist_ok=True)
    clear(folder)
    name = f"background-{time.time_ns()}{ext}"
    with open(os.path.join(folder, name), "wb") as f:
        f.write(data)
    return name


def _stored(folder: str) -> list[str]:
    return glob.glob(os.path.join(folder, "background*"))


def save(folder: str, source: str) -> str:
    ext = os.path.splitext(source)[1].lower()
    if ext not in MIME:
        raise OSError(f"unsupported image type '{ext or '?'}' (use PNG, JPG or WebP)")
    size = os.path.getsize(source)
    if size > MAX_BYTES:
        raise OSError(f"image is {size // (1024 * 1024)} MB; the limit is {MAX_BYTES // (1024 * 1024)} MB")
    os.makedirs(folder, exist_ok=True)
    clear(folder)
    # A new name per picture, so the panel notices a change of picture.
    dest = os.path.join(folder, f"background-{time.time_ns()}{ext}")
    shutil.copyfile(source, dest)
    return os.path.basename(dest)


def clear(folder: str) -> None:
    for old in _stored(folder):
        os.remove(old)


def data_url(folder: str) -> str | None:
    found = _stored(folder)
    if not found:
        return None
    path = found[0]
    mime = MIME.get(os.path.splitext(path)[1].lower())
    if not mime:
        return None
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()
