"""Tools bawaan agen: terminal, baca/tulis file, list direktori, load skill.

Didefinisikan sebagai JSON Schema OpenAI function calling, dieksekusi lewat
ToolExecutor dengan konfirmasi opsional untuk perintah terminal.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from .skills import SkillsRegistry

MAX_OUTPUT = 6000

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "run_terminal",
            "description": "Jalankan perintah shell dan kembalikan stdout/stderr. "
            "Gunakan untuk build, test, git, package manager, dll.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Perintah shell yang dijalankan."},
                    "timeout": {"type": "number", "description": "Timeout detik. Default 30."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Baca file teks dan kembalikan isinya.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path file."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Buat atau timpa file teks dengan konten yang diberikan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path file."},
                    "content": {"type": "string", "description": "Konten lengkap yang ditulis."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Daftar file & direktori dalam sebuah path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path direktori. Default '.'."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": "Muat instruksi lengkap sebuah skill (SKILL.md) ke konteks "
            "agar agen bisa mengikuti prosedurnya.",
            "parameters": {
                "type": "object",
                "properties": {"skill_name": {"type": "string", "description": "Nama skill."}},
                "required": ["skill_name"],
            },
        },
    },
]


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n...[dipotong, total {len(text)} karakter]"
    return text


class ToolExecutor:
    def __init__(self, confirm: Optional[Callable[[str], bool]] = None, cwd: Optional[Path] = None,
                 skills: Optional[SkillsRegistry] = None):
        self.confirm = confirm or (lambda _cmd: True)
        self.cwd = Path(cwd or os.getcwd())
        self.skills = skills

    def execute(self, name: str, args: dict) -> str:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return f"ERROR: tool tidak dikenal '{name}'"
        try:
            result = handler(args or {})
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:  # noqa: BLE001 - kegagalan tool tidak mematikan loop
            return f"ERROR: {type(e).__name__}: {e}"

    def _tool_run_terminal(self, args) -> str:
        cmd = str(args.get("command", "")).strip()
        timeout = int(args.get("timeout", 30) or 30)
        if not cmd:
            return "ERROR: perintah kosong"
        if not self.confirm(f"Jalankan: {cmd}"):
            return "CANCELLED oleh user"
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout, cwd=str(self.cwd))
        out = (proc.stdout or "") + (proc.stderr or "")
        out = out.strip()
        if not out:
            return f"(exit code {proc.returncode}, tanpa output)"
        return f"(exit code {proc.returncode})\n{_truncate(out)}"

    def _tool_read_file(self, args) -> str:
        p = self._resolve(args.get("path", ""))
        if not p.exists():
            return f"ERROR: file tidak ditemukan: {p}"
        content = p.read_text(encoding="utf-8", errors="replace")
        return _truncate(content)

    def _tool_write_file(self, args) -> str:
        p = self._resolve(args.get("path", ""))
        content = str(args.get("content", ""))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Ditulis {len(content)} karakter ke {p}"

    def _tool_list_directory(self, args) -> str:
        p = self._resolve(args.get("path", "."))
        if not p.exists():
            return f"ERROR: path tidak ditemukan: {p}"
        try:
            entries = sorted(os.listdir(p))
        except PermissionError as e:
            return f"ERROR: {e}"
        lines = []
        for e in entries[:200]:
            full = p / e
            kind = "dir " if full.is_dir() else "file"
            lines.append(f"{kind} {e}")
        if len(entries) > 200:
            lines.append(f"... dan {len(entries) - 200} lagi")
        return "\n".join(lines) or "(direktori kosong)"

    def _tool_load_skill(self, args) -> str:
        if self.skills is None:
            return "ERROR: registry skill tidak tersedia"
        name = str(args.get("skill_name", "")).strip()
        skill = self.skills.get(name)
        if skill is None:
            return f"ERROR: skill '{name}' tidak ditemukan"
        md = skill.load_markdown()
        if md is None:
            return f"ERROR: gagal membaca skill '{name}'"
        return md

    def _resolve(self, path: str) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.cwd / p
        return p
