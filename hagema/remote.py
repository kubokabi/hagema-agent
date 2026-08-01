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
from .history import HistoryRecorder
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
    source: str = "remote",
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
    history = HistoryRecorder(cfg.history_dir, session.path.stem, source=source)
    agent = Agent(cfg, pm, session, executor, skills, memory, history=history)
    return HeadlessBridge(cfg, pm, session, skills, memory, agent, yes=yes, cwd=Path(cwd))


class HeadlessBridge:
    """API headless untuk remote access (web / Telegram)."""

    def __init__(self, cfg, pm, session, skills, memory, agent, yes: bool = False,
                 cwd: Optional[Path] = None):
        self.cfg = cfg
        self.pm = pm
        self.session = session
        self.skills = skills
        self.memory = memory
        self.agent = agent
        self.yes = yes
        self.cwd = cwd if cwd is not None else Path(".")

    def run_cli_agent(self, name: str, prompt: str) -> str:
        """Jalankan CLI agent lain (opencode/hermes/dll) lewat remote.

        Keamanan: mode remote menolak eksekusi kecuali diberi `--yes`.
        """
        name = (name or "").strip().lower()
        prompt = (prompt or "").strip()
        if not name or not prompt:
            return "Pakai: <nama-agent> <prompt>"
        if not self.yes:
            return (
                "⛔ DITOLAK: kontrol CLI agent lain dari remote butuh izin. "
                "Jalankan server/web dengan flag --yes untuk mengizinkan."
            )
        from .agents import run_cli_agent as _run
        ok, out = _run(name, prompt, cwd=str(self.cwd))
        if not ok:
            return f"ERROR: {out}"
        return out

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
