"""
Libadwaita Application entry point for Batuque.
"""

import os
import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, Adw, Gio

from .main_window import MainWindow


class BatuqueApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.batuque.Batuque",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        if display:
            icon_theme = Gtk.IconTheme.get_for_display(display)
            # Search local data/icons if running from development tree
            repo_icons = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "icons"))
            if os.path.isdir(repo_icons):
                icon_theme.add_search_path(repo_icons)
            user_icons = os.path.expanduser("~/.local/share/icons")
            if os.path.isdir(user_icons):
                icon_theme.add_search_path(user_icons)
        Gtk.Window.set_default_icon_name("org.batuque.Batuque")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = MainWindow(application=self)
            win.set_icon_name("org.batuque.Batuque")
        win.present()


# Compatibility alias
SoundBeeApp = BatuqueApp


def run_app():
    app = BatuqueApp()
    # Filter out non-GTK args if any were passed
    return app.run([sys.argv[0]])
