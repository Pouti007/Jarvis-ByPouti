"""Obsidian vault integration: read/write notes, search, and daily conversation logging."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class NoteMatch:
    path: Path
    title: str
    snippet: str


def _slugify(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title).strip()
    slug = re.sub(r"[-\s]+", " ", slug)
    return slug or "untitled"


class ObsidianVault:
    """Thin filesystem wrapper around an Obsidian vault (plain .md files, no plugin API needed)."""

    def __init__(self, vault_path: str, daily_folder: str = "Daily", jarvis_folder: str = "Jarvis"):
        self.vault_path = Path(vault_path)
        self.daily_dir = self.vault_path / daily_folder
        self.jarvis_dir = self.vault_path / jarvis_folder
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.jarvis_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ notes

    def create_note(self, title: str, content: str, tags: list[str] | None = None) -> Path:
        tags = tags or []
        frontmatter = "---\n"
        frontmatter += f"created: {datetime.now().isoformat(timespec='seconds')}\n"
        if tags:
            frontmatter += "tags: [" + ", ".join(tags) + "]\n"
        frontmatter += "---\n\n"

        note_path = self.jarvis_dir / f"{_slugify(title)}.md"
        # avoid clobbering an existing note with the same title
        counter = 2
        while note_path.exists():
            note_path = self.jarvis_dir / f"{_slugify(title)} {counter}.md"
            counter += 1

        note_path.write_text(f"{frontmatter}# {title}\n\n{content}\n", encoding="utf-8")
        return note_path

    def read_note(self, path: str | Path) -> str:
        p = Path(path)
        if not p.is_absolute():
            p = self.vault_path / p
        return p.read_text(encoding="utf-8")

    def search_vault(self, keyword: str, max_results: int = 8) -> list[NoteMatch]:
        keyword_lower = keyword.lower()
        matches: list[NoteMatch] = []
        for md_file in self.vault_path.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lower = text.lower()
            idx = lower.find(keyword_lower)
            if idx == -1:
                continue
            start = max(0, idx - 80)
            end = min(len(text), idx + 160)
            snippet = text[start:end].replace("\n", " ").strip()
            matches.append(NoteMatch(path=md_file, title=md_file.stem, snippet=snippet))
            if len(matches) >= max_results:
                break
        return matches

    def recent_notes_summary(self, limit: int = 10) -> str:
        """Short summary of recently modified notes, used to give Claude vault context."""
        files = sorted(
            self.vault_path.rglob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
        if not files:
            return "(vault is empty)"
        lines = []
        for f in files:
            rel = f.relative_to(self.vault_path)
            lines.append(f"- {rel}")
        return "\n".join(lines)

    # ------------------------------------------------------------- daily log

    def read_recent_log(self, max_chars: int = 1500) -> str:
        """Return the tail of today's conversation log, for cross-session continuity.

        Kept short and pulled from disk (no extra API call) so seeding context
        into the prompt stays cheap.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        daily_path = self.daily_dir / f"{today}.md"
        if not daily_path.exists():
            return ""
        text = daily_path.read_text(encoding="utf-8", errors="ignore")
        return text[-max_chars:].strip()

    def log_conversation(self, user_text: str, jarvis_text: str) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        daily_path = self.daily_dir / f"{today}.md"
        timestamp = datetime.now().strftime("%H:%M")

        entry = f"\n**{timestamp} — You:** {user_text}\n\n**{timestamp} — Jarvis:** {jarvis_text}\n"

        if not daily_path.exists():
            header = f"---\ntags: [daily, jarvis-log]\n---\n\n# {today}\n\n## Jarvis Conversation Log\n"
            daily_path.write_text(header + entry, encoding="utf-8")
        else:
            with daily_path.open("a", encoding="utf-8") as f:
                f.write(entry)
        return daily_path
