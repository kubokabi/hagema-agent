"""Kontrol jarak jauh: bridge headless untuk web app & Telegram bot.

Bridge ini membangun komponen agen (config, provider, sesi, skill, memori,
tools) TANPA console REPL, lalu memaparkan API sederhana: chat(), status(),
usage(), reset(). Dipakai oleh subcommand `hagema serve` (web) dan
`hagema telegram` (bot).

Keamanan: di mode headless tidak ada konfirmasi interaktif untuk perintah
terminal. Tanpa flag `--yes`, tool run_terminal OTOMATIS DITOLAK
(mengembalikan "CANCELLED"). Akses jarak jauh sebaiknya dibatasi (token web,
daftar chat_id Telegram, atau Tailscale).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .agent import Agent
from .config import Config
from .memory import Memory
from .providers import ProviderManager
from .session import Session
from .skills import SkillsRegistry
from .tools import ToolExecutor


def build_bridge(
    cfg_path: Path,
    env_path: Path,
    session_name: Optional[str] = None,
    cwd: str = ".",
    yes: bool = False,
    pm: Optional[ProviderManager] = None,
) -> "HeadlessBridge":
    """Bangun HeadlessBridge dari path config/env (atau ProviderManager siap pakai)."""
    cfg = Config.load(cfg_path)
    cfg.load_env(env_path)

    if pm is None:
        pm = ProviderManager(cfg, os.environ)

    session = Session.create(cfg.sessions_dir, session_name)
    skills = SkillsRegistry(cfg.skills_dir)
    memory = Memory(cfg.memory_file)
    executor = ToolExecutor(
        confirm=(lambda _cmd: True) if yes else (lambda _cmd: False),
        cwd=Path(cwd),
        skills=skills,
    )
    agent = Agent(cfg, pm, session, executor, skills, memory)
    return HeadlessBridge(cfg, pm, session, skills, memory, agent)


class HeadlessBridge:
    """API headless untuk remote access (web / Telegram)."""

    def __init__(self, cfg, pm, session, skills, memory, agent):
        self.cfg = cfg
        self.pm = pm
        self.session = session
        self.skills = skills
        self.memory = memory
        self.agent = agent

    def chat(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return "(pesan kosong)"
        try:
            return self.agent.run(text)
        except Exception as e:  # noqa: BLE001 - tampilkan pesan mentah ke remote
            return f"ERROR: {type(e).__name__}: {e}"

    def reset(self) -> str:
        self.session.messages = []
        self.session.recap = None
        self.session.save()
        return "Sesi dibersihkan."

    def status(self) -> str:
        lines = [
            f"Provider aktif: {self.pm.current_name} → {self.pm.current.cfg.model}",
            f"Sesi: {self.session.path}",
        ]
        return "\n".join(lines)

    def usage(self) -> str:
        return self.agent.usage_text()

    def providers_text(self) -> str:
        out = []
        for name, p in self.pm._providers.items():
            marker = "★ aktif" if name == self.pm.current_name else "   "
            key_ok = p.cfg.has_key(os.environ)
            key_state = "key OK" if key_ok else "key KOSONG"
            out.append(f"{marker} {name} → {p.cfg.model} [{key_state}]")
        return "\n".join(out)

    def switch(self, name: str) -> str:
        target = self.pm.get(name)
        if target is None:
            return f"Provider '{name}' tidak ada. Tersedia: {', '.join(self.pm.names())}"
        old = self.pm.current_name
        recap = self.session.generate_recap(self.pm.current)
        if recap:
            self.session.compress_tail(4)
        self.pm.current_name = name
        return f"Provider: {old} → {name} (recap: {'✓' if recap else '✗'})"
