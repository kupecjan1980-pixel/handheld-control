"""Add a folder to Steam as a library, from outside Steam's UI (HHC Desktop).

1. Steam running with its CEF debugger on (Decky turns it on): Steam's own
   SteamClient.InstallFolder.AddInstallFolder, run in its SharedJSContext.
2. Steam closed: written into libraryfolders.vdf, as Steam writes it; Steam
   takes it on its next start.
3. Steam running without the debugger: kept as pending and written the moment
   Steam is closed (tick()), so it's there on the next start.
"""

import base64
import json
import logging
import os
import random
import re
import socket
import struct
import urllib.request

log = logging.getLogger("hhctl")

CEF = ("127.0.0.1", 8080)
ROOTS = (".local/share/Steam", ".steam/steam", ".var/app/com.valvesoftware.Steam/.local/share/Steam")
PENDING = "/var/lib/handheld-control/steam-libraries.json"


# --- Steam's CEF debugger (a minimal WebSocket client) --------------------------

def _ws_eval(ws_url: str, expr: str, timeout: float = 5.0) -> dict:
    m = re.match(r"ws://([^/:]+):(\d+)(/.*)", ws_url)
    if not m:
        raise OSError(f"bad debugger URL {ws_url}")
    host, port, path = m.group(1), int(m.group(2)), m.group(3)
    with socket.create_connection((host, port), timeout=timeout) as s:
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = s.recv(1024)
            if not chunk:
                raise OSError("debugger closed the connection")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise OSError("debugger refused the connection")
        buf = head.split(b"\r\n\r\n", 1)[1]
        msg = json.dumps({"id": 1, "method": "Runtime.evaluate",
                          "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}).encode()
        mask = os.urandom(4)
        n = len(msg)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(msg)))

        def need(k: int) -> bytes:
            nonlocal buf
            while len(buf) < k:
                chunk = s.recv(65536)
                if not chunk:
                    raise OSError("debugger closed the connection")
                buf += chunk
            out, buf = buf[:k], buf[k:]
            return out

        while True:
            b0, b1 = need(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", need(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", need(8))[0]
            data = need(n)
            if b0 & 0x0F == 1:
                r = json.loads(data)
                if r.get("id") == 1:
                    return r


def _add_via_cef(path: str) -> bool:
    try:
        with urllib.request.urlopen(f"http://{CEF[0]}:{CEF[1]}/json", timeout=2) as r:
            targets = json.load(r)
    except (OSError, ValueError):
        return False
    t = next((t for t in targets if t.get("title") == "SharedJSContext" and t.get("webSocketDebuggerUrl")), None)
    if not t:
        return False
    try:
        r = _ws_eval(t["webSocketDebuggerUrl"],
                     f"(async () => {{ await SteamClient.InstallFolder.AddInstallFolder({json.dumps(path)}); return true; }})()")
    except (OSError, ValueError) as e:
        log.warning("steamlib: debugger: %s", e)
        return False
    ok = r.get("result", {}).get("result", {}).get("value") is True
    if not ok:
        log.warning("steamlib: AddInstallFolder failed: %s", r)
    return ok


# --- libraryfolders.vdf -----------------------------------------------------------

def _steam_root(home: str) -> str | None:
    for r in ROOTS:
        p = os.path.join(home, r)
        if os.path.isfile(os.path.join(p, "steamapps", "libraryfolders.vdf")) or os.path.isdir(os.path.join(p, "config")):
            return os.path.realpath(p)
    return None


def steam_running(uid: int) -> bool:
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() in ("steam", "steamwebhelper"):
                    return True
        except OSError:
            continue
    return False


def _listed(text: str, path: str) -> bool:
    return any(os.path.realpath(p.replace("\\\\", "\\")) == os.path.realpath(path)
               for p in re.findall(r'"path"\s+"([^"]+)"', text))


def _add_entry(text: str, path: str, contentid: str) -> str:
    nums = [int(n) for n in re.findall(r'^\t"(\d+)"\s*$', text, re.M)]
    idx = max(nums) + 1 if nums else 1
    entry = (f'\t"{idx}"\n\t{{\n\t\t"path"\t\t"{path}"\n\t\t"label"\t\t""\n\t\t"contentid"\t\t"{contentid}"\n'
             f'\t\t"totalsize"\t\t"0"\n\t\t"update_clean_bytes_tally"\t\t"0"\n\t\t"time_last_update_verified"\t\t"0"\n'
             f'\t\t"apps"\n\t\t{{\n\t\t}}\n\t}}\n')
    end = text.rstrip().rfind("}")
    if end < 0:
        return f'"libraryfolders"\n{{\n{entry}}}\n'
    return text[:end] + entry + text[end:]


def _write_vdf(home: str, path: str) -> bool:
    """Into every libraryfolders.vdf Steam keeps; False when there's no Steam."""
    root = _steam_root(home)
    if not root:
        return False
    st = os.stat(home)
    contentid = str(random.getrandbits(62))
    for f in (os.path.join(root, "config", "libraryfolders.vdf"), os.path.join(root, "steamapps", "libraryfolders.vdf")):
        try:
            with open(f, encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            if not f.endswith("config/libraryfolders.vdf") or not os.path.isdir(os.path.dirname(f)):
                continue
            text = ""
        if _listed(text, path):
            continue
        tmp = f + ".hhc.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(_add_entry(text, path, contentid))
        os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, f)
    # The folder's own marker, as Steam makes it.
    os.makedirs(os.path.join(path, "steamapps"), exist_ok=True)
    marker = os.path.join(path, "libraryfolder.vdf")
    if not os.path.exists(marker):
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(f'"libraryfolder"\n{{\n\t"contentid"\t\t"{contentid}"\n\t"label"\t\t""\n}}\n')
    for p in (path, os.path.join(path, "steamapps"), marker):
        try:
            if os.stat(p).st_uid == 0:
                os.chown(p, st.st_uid, st.st_gid)
        except OSError:
            pass
    log.info("steamlib: %s added to libraryfolders.vdf", path)
    return True


# --- pending ------------------------------------------------------------------------

def _pending() -> list[dict]:
    try:
        with open(PENDING) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save_pending(items: list[dict]) -> None:
    if not items:
        try:
            os.remove(PENDING)
        except FileNotFoundError:
            pass
        return
    os.makedirs(os.path.dirname(PENDING), exist_ok=True)
    with open(PENDING, "w") as f:
        json.dump(items, f)


def add(home: str, path: str) -> str:
    """Add it; returns what happened, for the panel."""
    uid = os.stat(home).st_uid
    if steam_running(uid):
        if _add_via_cef(path):
            return f"Added to Steam: {path}"
        items = [i for i in _pending() if i["path"] != path] + [{"home": home, "path": path}]
        _save_pending(items)
        return f"Steam is open, so it's added the moment you close Steam; it's in the library on the next start ({path})"
    if _write_vdf(home, path):
        return f"Added to Steam: {path} (shows up when Steam starts)"
    return f"Steam isn't set up for this user yet; in Steam, open Settings › Storage › Add drive and pick {path}"


def tick() -> None:
    """Pending libraries go in as soon as Steam is closed."""
    items = _pending()
    if not items:
        return
    left = []
    for i in items:
        try:
            uid = os.stat(i["home"]).st_uid
            if steam_running(uid) or not os.path.isdir(i["path"]):
                left.append(i)
                continue
            _write_vdf(i["home"], i["path"])
        except OSError as e:
            log.warning("steamlib: %s: %s", i.get("path"), e)
    _save_pending(left)
