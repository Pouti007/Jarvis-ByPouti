"""System stats sidebar: CPU, RAM, and (best-effort) GPU readings, updated live."""

from __future__ import annotations

import psutil
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

# AMD integrated GPU temperature isn't exposed via any cross-vendor stdlib API;
# without a vendor tool (e.g. AMD Software) or WMI sensor bridge installed, we
# just report utilization is unavailable rather than guessing.


class SystemStatsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("SYSTEM", objectName="panelTitle"))
        self.cpu_label = QLabel("CPU: --%")
        self.ram_label = QLabel("RAM: --%")
        self.gpu_label = QLabel("GPU: N/A")
        for lbl in (self.cpu_label, self.ram_label, self.gpu_label):
            layout.addWidget(lbl)
        layout.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1500)
        self._refresh()

    def _refresh(self) -> None:
        self.cpu_label.setText(f"CPU: {psutil.cpu_percent():.0f}%")
        self.ram_label.setText(f"RAM: {psutil.virtual_memory().percent:.0f}%")
