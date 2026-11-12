"""Main JARVIS HUD dashboard window: dark theme, cyan/blue Iron Man-inspired accents."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMainWindow, QVBoxLayout, QWidget

from gui.panels.pulse import PulseWidget
from gui.panels.second_brain import SecondBrainPanel
from gui.panels.stats import SystemStatsPanel
from gui.panels.transcript import TranscriptPanel
from jarvis.obsidian import ObsidianVault

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #060a10;
    color: #cdeefb;
    font-family: "Segoe UI", "Consolas", sans-serif;
    font-size: 13px;
}
QLabel#appTitle {
    color: #00d9ff;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 6px;
}
QLabel#appSubtitle {
    color: #3a6f88;
    font-size: 10px;
    letter-spacing: 4px;
}
QLabel#panelTitle {
    color: #00d9ff;
    font-weight: 600;
    font-size: 12px;
    letter-spacing: 2px;
    padding-bottom: 4px;
    border-bottom: 1px solid #14384f;
}
QTextEdit#transcriptView {
    background-color: #0a1018;
    border: 1px solid #14384f;
    border-radius: 6px;
    padding: 8px;
}
QLineEdit {
    background-color: #0a1018;
    border: 1px solid #14384f;
    border-radius: 4px;
    padding: 6px;
    color: #cdeefb;
    selection-background-color: #00d9ff;
}
QLineEdit:focus {
    border: 1px solid #00d9ff;
}
QListWidget {
    background-color: #0a1018;
    border: 1px solid #14384f;
    border-radius: 6px;
    padding: 4px;
}
QLabel#statusLabel {
    color: #00d9ff;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 4px;
}
"""


class Dashboard(QMainWindow):
    # Emitted when the user types a command and presses Enter.
    command_entered = pyqtSignal(str)

    def __init__(self, vault: ObsidianVault):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S.")
        self.resize(1100, 650)
        self.setStyleSheet(DARK_STYLESHEET)

        central = QWidget(self)
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        # left: system stats sidebar
        self.stats_panel = SystemStatsPanel(self)
        self.stats_panel.setFixedWidth(160)
        root.addWidget(self.stats_panel)

        # center: title + pulse + transcript + status
        center = QWidget(self)
        center_layout = QVBoxLayout(center)

        title = QLabel("J.A.R.V.I.S.", objectName="appTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle = QLabel("COMMAND CENTER", objectName="appSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.pulse = PulseWidget(center)
        self.status_label = QLabel("IDLE", objectName="statusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.transcript_panel = TranscriptPanel(center)

        # Text input: type a command instead of speaking it.
        self.command_input = QLineEdit(center)
        self.command_input.setPlaceholderText("Type a command and press Enter…")
        self.command_input.returnPressed.connect(self._on_command_entered)

        center_layout.addWidget(title)
        center_layout.addWidget(subtitle)
        center_layout.addWidget(self.pulse, stretch=1)
        center_layout.addWidget(self.status_label)
        center_layout.addWidget(self.transcript_panel, stretch=1)
        center_layout.addWidget(self.command_input)
        root.addWidget(center, stretch=1)

        # right: second brain
        self.second_brain_panel = SecondBrainPanel(vault, self)
        self.second_brain_panel.setFixedWidth(280)
        root.addWidget(self.second_brain_panel)

        self._panels = {
            "stats": self.stats_panel,
            "transcript": self.transcript_panel,
            "second_brain": self.second_brain_panel,
        }

    # ---------------------------------------------------------- input handling

    def _on_command_entered(self) -> None:
        text = self.command_input.text().strip()
        if not text:
            return
        self.command_input.clear()
        self.command_entered.emit(text)

    # ---------------------------------------------------------- public API

    def set_state(self, state: str) -> None:
        self.pulse.set_state(state)
        self.status_label.setText(state.upper())

    def add_transcript(self, speaker: str, text: str) -> None:
        self.transcript_panel.add_entry(speaker, text)

    def refresh_notes(self) -> None:
        self.second_brain_panel.refresh_recent()

    def toggle_panel(self, name: str, visible: bool | None = None) -> None:
        panel = self._panels.get(name)
        if panel is None:
            return
        panel.setVisible(not panel.isVisible() if visible is None else visible)

    # ---------------------------------------------------------- window behaviour

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Minimize to tray instead of quitting; the tray menu handles a real quit."""
        event.ignore()
        self.hide()
