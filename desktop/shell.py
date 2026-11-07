#!/usr/bin/env python3
"""HHC Desktop's own window (WebKitGTK), where WebKit2 4.1 is there: the same panel as in the browser window, but
the close button minimises it to its taskbar icon instead of ending it (a browser window can't be kept from
closing). Quit: Ctrl+Q.   shell.py <url>"""

import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gdk, Gtk, WebKit2  # noqa: E402

GLib.set_prgname("hhc-desktop")  # the window's app id: its own taskbar icon (hhc-desktop.desktop)
GLib.set_application_name("HHC Desktop")


def main() -> int:
    url = sys.argv[1]
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

    def close(*_):  # closing: to the taskbar icon, still running (and still putting the settings into effect)
        win.iconify()
        return True
    # (Hyprland has no minimising and no taskbar: there it closes as before, or it could never be closed)
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        win.connect("delete-event", close)

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
