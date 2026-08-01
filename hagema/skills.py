"""Sistem skill mengikuti standar agentskills.io.

Setiap skill adalah direktori berisi SKILL.md dengan frontmatter YAML
(name, description). Saat startup hanya daftar nama+deskripsi yang dimuat
(progressive disclosure level 0); isi lengkap baru dimuat saat dipanggil.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class Skill:
    name: str
    description: str
    path: Path

    def load_markdown(self) -> Optional[str]:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return None


class SkillsRegistry:
    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir)
        self._skills: Dict[str, Skill] = {}
        self.refresh()

    def refresh(self) -> None:
        self._skills = {}
        if not self.skills_dir.exists():
            return
        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            md = skill_dir / "SKILL.md"
            if not md.exists():
                continue
            meta = _parse_frontmatter(md)
            name = meta.get("name") or skill_dir.name
            desc = meta.get("description") or "(tanpa deskripsi)"
            self._skills[name] = Skill(name=name, description=desc, path=md)

    def list(self) -> List[Skill]:
        return [self._skills[n] for n in sorted(self._skills)]

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name) or self._skills.get(name.lower())

    def index_text(self) -> str:
        """Ringkasan level-0 untuk dimuat ke system prompt."""
        if not self._skills:
            return "(tidak ada skill. Buat di ~/.hagema/skills/<nama>/SKILL.md)"
        return "\n".join(f"- {s.name}: {s.description}" for s in self.list())


def _parse_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        return yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
