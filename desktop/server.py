#!/usr/bin/env python3
"""Handheld Control on a desktop: the plugin's real backend behind a local web server.

The panel (desktop/dist, built from the same src/index.tsx) calls the
backend's methods over HTTP, like Decky's callable() does. Run as root to
let it change hardware settings; as a normal user it can only read.

Installed (desktop/install.sh), systemd starts it as root when the app
connects (socket activation on 127.0.0.1:8765) and it exits when the window
closes; it then makes its own token and leaves it in --token-file, readable
only by the desktop user.

Only 127.0.0.1, and every request needs the token, so web pages and other
users can't drive it. Everything the plugin changed is put back when the
server stops (window closed, Ctrl+C), as on plugin unload.
"""

import argparse
import asyncio
import json
import logging
import os
import queue
import secrets
import signal
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(HERE, "dist")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", help="request token; '-' reads it from stdin")
    ap.add_argument("--token-file", help="make a token and write it here, readable only by --uid")
    ap.add_argument("--home", default=os.path.expanduser("~"), help="the desktop user's home")
    ap.add_argument("--uid", type=int, default=os.getuid(), help="owner for the settings files")
    args = ap.parse_args()
    if args.token == "-":
        args.token = sys.stdin.readline().strip()
    if not args.token:
        if not args.token_file:
            ap.error("--token or --token-file is needed")
        args.token = secrets.token_hex(16)

    os.environ["HHC_USER_HOME"] = args.home
    sys.path[:0] = [HERE, ROOT, os.path.join(ROOT, "py_modules")]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import decky  # the stand-in in this folder
    import main as plugin_main

    clients: list[queue.Queue] = []
    lock = threading.Lock()

    def sink(event, payload):
        with lock:
            for q in clients:
                q.put((event, payload))

    decky.sink = sink
    loop = asyncio.new_event_loop()
    plugin = plugin_main.Plugin()

    def own_settings():
        # Root writes the settings; keep them the desktop user's.
        for d, _, files in os.walk(decky.DECKY_PLUGIN_SETTINGS_DIR):
            for p in [d, *(os.path.join(d, f) for f in files)]:
                try:
                    os.chown(p, args.uid, -1)
                except OSError:
                    pass

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def _authed(self) -> bool:
            q = parse_qs(urlparse(self.path).query)
            return self.headers.get("X-HHC-Token") == args.token or q.get("token", [None])[0] == args.token

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/ping":
                return self._send(200, b'"ok"')
            if path == "/events":
                if not self._authed():
                    return self._send(403, b'"forbidden"')
                return self._events()
            name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
            file = os.path.realpath(os.path.join(DIST, name))
            if not file.startswith(DIST + os.sep) or not os.path.isfile(file):
                return self._send(404, b'"not found"')
            with open(file, "rb") as f:
                self._send(200, f.read(), TYPES.get(os.path.splitext(file)[1], "application/octet-stream"))

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._authed():
                return self._send(403, b'"forbidden"')
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if path == "/quit":
                self._send(200, b'"bye"')
                return loop.call_soon_threadsafe(loop.stop)
            method = path.removeprefix("/api/")
            fn = getattr(plugin, method, None)
            if method.startswith("_") or not callable(fn):
                return self._send(404, json.dumps(f"no method {method}").encode())
            try:
                call_args = json.loads(body or b"[]")
                result = asyncio.run_coroutine_threadsafe(fn(*call_args), loop).result(timeout=120)
                own_settings()
                self._send(200, json.dumps(result).encode())
            except Exception as e:
                logging.exception("%s failed", method)
                self._send(500, json.dumps(str(e)).encode())

        def _events(self):
            q: queue.Queue = queue.Queue()
            with lock:
                clients.append(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    try:
                        event, payload = q.get(timeout=15)
                        msg = f"data: {json.dumps({'event': event, 'args': payload})}\n\n"
                    except queue.Empty:
                        msg = ": keepalive\n\n"
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with lock:
                    clients.remove(q)

    asyncio.set_event_loop(loop)
    loop.run_until_complete(plugin._main())
    # Installing a release would overwrite this repository: the updater is for the handheld.
    plugin.ctl.plugin_dir = None

    def no_install(tag):
        plugin.ctl.upd["error"] = "The desktop app runs from the repository; install releases on the handheld."
        return plugin.ctl.state()

    plugin.ctl.install_update = no_install
    own_settings()

    if args.token_file:
        # Written before the first request is answered, so the launcher finds it.
        fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(args.token)
        os.chown(args.token_file, args.uid, -1)

    if os.environ.get("LISTEN_PID") == str(os.getpid()) and int(os.environ.get("LISTEN_FDS", "0")) >= 1:
        # Started by hhc-desktop.socket: systemd hands over the listening socket as fd 3.
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler, bind_and_activate=False)
        httpd.socket = socket.socket(fileno=3)
        httpd.server_address = httpd.socket.getsockname()
    else:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)
    logging.info("Handheld Control desktop: http://127.0.0.1:%d (root: %s)", args.port, os.geteuid() == 0)
    try:
        loop.run_forever()
    finally:
        httpd.shutdown()
        loop.run_until_complete(plugin._unload())
        own_settings()
        logging.info("stopped; settings put back")


if __name__ == "__main__":
    main()
