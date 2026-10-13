"""
Libadwaita Application entry point for SoundBee.
"""

import sys
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio

from .main_window import MainWindow


class SoundBeeApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.soundbee.SoundBee",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = MainWindow(application=self)
        win.present()


def run_app():
    app = SoundBeeApp()
    return app.run(sys.argv)
