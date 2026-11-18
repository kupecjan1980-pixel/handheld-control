#!/usr/bin/env python3
"""HHC Desktop's own window (WebKitGTK), where WebKit2 4.1 is there: the same panel as in the browser window, but
closing it (its X, or "Close" on its taskbar button) only hides it: the backend goes on putting the settings into
effect, and opening HHC Desktop again shows the same window (one runs at a time; a second start hands over to it
and exits with HANDED_OVER, so the launcher leaves the backend running). Quit, which puts everything back: Ctrl+Q.
   shell.py <url>"""

import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gdk, Gtk, WebKit2  # noqa: E402

GLib.set_prgname("hhc-desktop")  # the window's app id: its own taskbar icon (hhc-desktop.desktop)
GLib.set_application_name("HHC Desktop")
APP_ID = "io.github.kupecjan1980.HhcDesktop"  # (only for one-at-a-time: the window isn't added to it, so its
HANDED_OVER = 3                               # Wayland app id stays hhc-desktop, the taskbar icon's)


def main() -> int:
    url = sys.argv[1]
    app = Gtk.Application(application_id=APP_ID)
    try:
        app.register(None)
    except GLib.Error:
        app = None  # (no session bus: just this window, as before)
    if app is not None and app.get_is_remote():
        app.activate()  # the running one shows its window
        return HANDED_OVER
    win = Gtk.Window(title="HHC Desktop")
    win.set_default_size(420, 900)
    try:
        win.set_icon_name("hhc-desktop")
    except GLib.Error:
        pass
    view = WebKit2.WebView()
    settings = view.get_settings()
    settings.set_enable_developer_extras(False)
    view.load_uri(url)
    win.add(view)

    def close(*_):
        # closing: the window goes, HHC stays (the user: X and the taskbar's "Zavřít" only minimised it before,
        # and quitting would put TDP, fan and the rest back): hidden, still running, shown again by a new start
        win.hide()
        return True
    # (Hyprland has no taskbar to bring it back from: there it closes as before)
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        win.connect("delete-event", close)
    if app is not None:
        app.hold()  # (kept: it has no window of its own)
        app.connect("activate", lambda *_: (win.show_all(), win.present()))

    def key(_w, ev):
        if ev.keyval in (Gdk.KEY_q, Gdk.KEY_Q) and ev.state & Gdk.ModifierType.CONTROL_MASK:
            Gtk.main_quit()
            return True
        return False
    win.connect("key-press-event", key)
    view.connect("close", lambda *_: Gtk.main_quit())  # (the page asking to close: window.close())
    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
