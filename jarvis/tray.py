"""System tray icon: minimize-to-tray with a menu to restore or quit."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pystray
from PIL import Image

ICON_PATH = Path(__file__).parent.parent / "gui" / "assets" / "icon.png"


class TrayIcon:
    def __init__(self, on_show: Callable[[], None], on_quit: Callable[[], None]):
        self.on_show = on_show
        self.on_quit = on_quit
        image = Image.open(ICON_PATH) if ICON_PATH.exists() else Image.new("RGBA", (64, 64), (0, 217, 255, 255))
        menu = pystray.Menu(
            pystray.MenuItem("Show JARVIS", self._show, default=True),
            pystray.MenuItem("Quit", self._quit),
        )
        self.icon = pystray.Icon("jarvis", image, "JARVIS", menu)

    def _show(self, icon, item) -> None:
        self.on_show()

    def _quit(self, icon, item) -> None:
        self.icon.stop()
        self.on_quit()

    def run_detached(self) -> None:
        """Starts the tray icon loop on a background thread (non-blocking)."""
        self.icon.run_detached()

    def stop(self) -> None:
        self.icon.stop()
