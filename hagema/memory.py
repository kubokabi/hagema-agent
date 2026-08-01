"""Memori jangka panjang berbasis file MEMORY.md.

Isi MEMORY.md selalu dimuat ke system prompt di setiap sesi, mirip
"prompt memory" pada Hermes Agent.
"""

from __future__ import annotations

from pathlib import Path


class Memory:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8").strip()

    def append(self, text: str) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = f"- {text.strip()}\n"
        existing = self.load()
        content = existing + "\n" + entry if existing else entry
        self.path.write_text(content, encoding="utf-8")
        return entry.strip()
