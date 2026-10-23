"""Install any release of this plugin from GitHub, from the panel.

Typing a release URL on a handheld is tedious, so the panel lists the
GitHub releases (pre-releases included) and installs the one picked: the zip
is downloaded, checked, unpacked next to the plugin folder and swapped in,
then Decky's plugin loader restarts to load it. Settings live in Decky's
settings folder, not the plugin folder, so they survive.

Only zips from this repository's release downloads are accepted.

Each release has two zips: handheld-control-<v>.zip (the Decky plugin) and
handheld-control-desktop-<v>.zip (the plugin plus the desktop app's server
and panel, installed in /opt by desktop/install.sh). `kind` picks one.
"""

import json
import logging
import os
import shutil
import ssl
import tempfile
import urllib.request
import zipfile

from . import proc

log = logging.getLogger("hhctl")

REPO = "kupecjan1980-pixel/handheld-control"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
PLUGIN_NAME = "Handheld Control"
MAX_ZIP = 50 * 1024 * 1024
TIMEOUT = 20
# Decky's bundled Python may have no CA store; use the system's.
CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Arch / CachyOS / Debian
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora / Bazzite
    "/etc/ssl/cert.pem",
)


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not ctx.cert_store_stats().get("x509_ca"):
        for ca in CA_FILES:
            if os.path.exists(ca):
                ctx.load_verify_locations(ca)
                break
    return ctx


def _get(url: str, limit: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "handheld-control", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx()) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise OSError(f"download is larger than {limit // (1024 * 1024)} MB")
    return data


def version_key(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in v.lstrip("v").split("."))


# What each kind of install must contain, besides plugin.json.
NEEDS = {"plugin": ("main.py", "dist/index.js"), "desktop": ("main.py", "desktop/server.py", "desktop/dist/bundle.js")}


def _is_kind(name: str, kind: str) -> bool:
    desktop = name.startswith("handheld-control-desktop-")
    return name.endswith(".zip") and (desktop if kind == "desktop" else not desktop)


def parse_releases(raw: list, kind: str = "plugin") -> list[dict]:
    out = []
    for r in raw:
        if r.get("draft"):
            continue
        zips = [a for a in r.get("assets", []) if _is_kind(a.get("name", ""), kind)]
        if not zips:
            continue
        url = zips[0].get("browser_download_url", "")
        if not url.startswith(DOWNLOAD_PREFIX):
            continue
        tag = r.get("tag_name", "")
        out.append({
            "tag": tag,
            "version": tag.lstrip("v"),
            "prerelease": bool(r.get("prerelease")),
            "date": (r.get("published_at") or "")[:10],
            "url": url,
            "notes": (r.get("body") or "").strip(),
        })
    out.sort(key=lambda x: version_key(x["version"]), reverse=True)
    return out


def releases(kind: str = "plugin") -> list[dict]:
    return parse_releases(json.loads(_get(API, 5 * 1024 * 1024)), kind)


def _check_zip(z: zipfile.ZipFile, kind: str = "plugin") -> None:
    root = PLUGIN_NAME + "/"
    for name in z.namelist():
        if not name.startswith(root) or ".." in name.split("/") or name.startswith("/"):
            raise OSError(f"unexpected file in the zip: {name}")
    try:
        meta = json.loads(z.read(root + "plugin.json"))
    except (KeyError, ValueError) as e:
        raise OSError(f"zip has no valid plugin.json ({e})")
    if meta.get("name") != PLUGIN_NAME:
        raise OSError(f"zip is for '{meta.get('name')}', not {PLUGIN_NAME}")
    for need in NEEDS[kind]:
        if root + need not in z.namelist():
            raise OSError(f"zip is missing {need}")


def _chown_tree(path: str, uid: int, gid: int) -> None:
    for d, dirs, files in os.walk(path):
        for n in [d, *(os.path.join(d, f) for f in files)]:
            try:
                os.chown(n, uid, gid)
            except OSError:
                pass


def install_zip(data: bytes, plugin_dir: str, kind: str = "plugin") -> None:
    """Swap the plugin folder for the zip's contents (old folder kept until it worked)."""
    plugin_dir = os.path.realpath(plugin_dir)
    parent, base = os.path.split(plugin_dir)
    with tempfile.TemporaryDirectory(dir=parent, prefix=".hhctl-update-") as tmp:
        zpath = os.path.join(tmp, "release.zip")
        with open(zpath, "wb") as f:
            f.write(data)
        with zipfile.ZipFile(zpath) as z:
            _check_zip(z, kind)
            z.extractall(tmp)
        new = os.path.join(tmp, PLUGIN_NAME)
        st = os.stat(plugin_dir)
        _chown_tree(new, st.st_uid, st.st_gid)
        old = os.path.join(tmp, "previous")
        os.rename(plugin_dir, old)
        try:
            os.rename(new, plugin_dir)
        except OSError:
            os.rename(old, plugin_dir)  # put the working version back
            raise
    # the temporary folder (with the previous version) is gone now


def install(url: str, plugin_dir: str, kind: str = "plugin") -> None:
    if not url.startswith(DOWNLOAD_PREFIX) or not _is_kind(url.rsplit("/", 1)[-1], kind):
        raise OSError("only this plugin's GitHub release zips can be installed")
    log.info("updater: installing %s (%s) into %s", url, kind, plugin_dir)
    install_zip(_get(url, MAX_ZIP), plugin_dir, kind)


def restart_loader() -> None:
    # Started in its own session: systemd carries the restart out after this
    # process (part of the loader) is stopped.
    proc.popen(["systemctl", "restart", "plugin_loader"], start_new_session=True)
