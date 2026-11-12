"""Second Brain panel: recent Obsidian notes plus a quick search box."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QLineEdit, QListWidget, QVBoxLayout, QWidget

from jarvis.obsidian import ObsidianVault


class SecondBrainPanel(QWidget):
    def __init__(self, vault: ObsidianVault, parent=None):
        super().__init__(parent)
        self.vault = vault

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("SECOND BRAIN", objectName="panelTitle"))

        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText("Search vault...")
        self.search_box.returnPressed.connect(self._on_search)
        layout.addWidget(self.search_box)

        self.list_widget = QListWidget(self)
        layout.addWidget(self.list_widget)

        self.refresh_recent()

    def refresh_recent(self) -> None:
        self.list_widget.clear()
        self.list_widget.addItem("Recent notes:")
        for line in self.vault.recent_notes_summary().splitlines():
            self.list_widget.addItem(line)

    def _on_search(self) -> None:
        keyword = self.search_box.text().strip()
        if not keyword:
            self.refresh_recent()
            return
        self.list_widget.clear()
        matches = self.vault.search_vault(keyword)
        if not matches:
            self.list_widget.addItem(f"No notes found for '{keyword}'.")
            return
        for m in matches:
            self.list_widget.addItem(f"{m.title}: {m.snippet}")
