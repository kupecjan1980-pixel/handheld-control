"""Custom background picture for the plugin's panel.

The chosen image is copied into the plugin's settings folder, so the panel
keeps working if the original is moved or deleted. The frontend can't load
local files directly, so it gets the picture once as a data URL.
"""

import base64
import glob
import os
import shutil
import time

MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
MAX_BYTES = 8 * 1024 * 1024  # it travels to the panel as base64


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
