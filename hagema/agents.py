"""Deteksi & kontrol CLI agent di mesin (`hagema agents`).

Memindai PATH untuk CLI agent coding populer (opencode, claude, codex,
aichat, gemini, aider, cursor, dll), melaporkan versi yang terpasang,
dan menawarkan perintah install untuk yang belum ada. Ini membuat
hagema-agent bisa "mengontrol" ekosistem agent lain di laptop/PC,
misalnya dari HP lewat Telegram (`/agents`) atau web (`/api/agents`).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Nama binary -> (label, daftar perintah install yang disarankan)
KNOWN_AGENTS: Dict[str, tuple] = {
    "opencode": (
        "OpenCode (open-source)",
        ["npm install -g opencode-ai"],
    ),
    "claude": (
        "Claude Code (Anthropic)",
        ["npm install -g @anthropic-ai/claude-code"],
    ),
    "codex": (
        "OpenAI Codex CLI",
        ["npm install -g @openai/codex"],
    ),
    "aichat": (
        "aichat (Rust, multi-provider)",
        ["cargo install aichat", "brew install aichat"],
    ),
    "gemini": (
        "Google Gemini CLI",
        ["npm install -g @google/gemini-cli"],
    ),
    "aider": (
        "Aider (pair programming)",
        ["pipx install aider-chat", "brew install aider"],
    ),
    "cursor": (
        "Cursor (editor + agent)",
        ["brew install --cask cursor"],
    ),
    "hermes": (
        "Hermes Agent (Nous Research)",
        ["pipx install hermes-agent", "git clone https://github.com/NousResearch/hermes-agent"],
    ),
}


@dataclass
class AgentInfo:
    name: str
    label: str
    installed: bool = False
    path: str = ""
    version: str = ""
    install_commands: List[str] = field(default_factory=list)

    def to_text(self, marker: str = "★") -> str:
        if self.installed:
            ver = f" v{self.version}" if self.version else ""
            return f"  {marker} {self.name} — {self.label} [TERPASANG{ver} · {self.path}]"
        return f"     {self.name} — {self.label} [belum ada]  →  install: {self.install_commands[0] if self.install_commands else '(manual)'}"


def _get_version(binary: str) -> str:
    """Coba beberapa flag umum untuk mendapatkan versi; diam saja kalau gagal."""
    for flag in ("--version", "-v", "version"):
        try:
            out = subprocess.run(
                [binary, flag],
                capture_output=True, text=True, timeout=4,
            )
            first = (out.stdout or out.stderr).strip().splitlines()
            if first:
                v = first[0].strip()
                if v and "usage" not in v.lower() and "error" not in v.lower():
                    return v[:60]
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


def detect_agents(extra: Optional[List[str]] = None) -> List[AgentInfo]:
    """Scan PATH: kembalikan daftar agent yang dikenal (terpasang atau tidak)."""
    results: List[AgentInfo] = []
    names = list(KNOWN_AGENTS.keys()) + [n for n in (extra or []) if n not in KNOWN_AGENTS]
    for name in names:
        label, installs = KNOWN_AGENTS.get(name, (name, []))
        path = shutil.which(name)
        if path:
            results.append(
                AgentInfo(
                    name=name, label=label, installed=True,
                    path=path, version=_get_version(name),
                    install_commands=list(installs),
                )
            )
        else:
            results.append(
                AgentInfo(name=name, label=label, install_commands=list(installs))
            )
    return results


def agents_text() -> str:
    """Teks ringkas untuk ditampilkan di Telegram / CLI."""
    detected = detect_agents()
    found = [a for a in detected if a.installed]
    missing = [a for a in detected if not a.installed]
    lines = ["🤖 CLI AGENT DI MESIN INI"]
    if found:
        lines.append("Terpasang:")
        lines.extend(a.to_text() for a in found)
    else:
        lines.append("Terpasang: (tidak ada yang terdeteksi)")
    if missing:
        lines.append("\nBelum terpasang (bisa diinstall):")
        lines.extend(a.to_text() for a in missing)
    lines.append("\nInstall dari terminal: [bold]hagema agents install <nama>[/bold]")
    return "\n".join(lines)


def install_command_for(name: str) -> Optional[str]:
    """Perintah install pertama yang disarankan untuk sebuah agent."""
    info = KNOWN_AGENTS.get(name)
    if not info:
        return None
    return info[1][0] if info[1] else None
