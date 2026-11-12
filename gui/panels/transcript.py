"""Live transcript panel: shows what JARVIS heard and said."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtWidgets import QTextEdit, QVBoxLayout, QWidget


class TranscriptPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.view = QTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setObjectName("transcriptView")
        layout.addWidget(self.view)

    def add_entry(self, speaker: str, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = "#00d9ff" if speaker.lower() == "jarvis" else "#8fe9ff"
        self.view.append(f'<span style="color:{color}"><b>[{timestamp}] {speaker}:</b></span> {text}')
