"""Riwayat percakapan lengkap untuk bahan belajar AI (`~/.hagema/history/`).

Semua interaksi (CLI, web, Telegram) direkam sedetail mungkin per giliran:
- teks user & balasan asisten
- tool calls beserta argumen & output
- provider aktif, model, token (in/out), estimasi biaya
- timestamp, sumber (cli/web/telegram), nama sesi

Format: JSONL per-sesi di `history/<YYYY-MM-DD>/<sesi>.jsonl`.
Data ini bisa dipakai untuk analisis, eval, atau bahan fine-tune nanti.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class HistoryRecorder:
    """Perekam riwayat: satu file JSONL per sesi, dikelompokkan per tanggal."""

    def __init__(self, history_dir: Path, session_name: str, source: str = "cli"):
        self.history_dir = Path(history_dir)
        self.session_name = session_name
        self.source = source
        self.day = time.strftime("%Y-%m-%d")
        self.path = self.history_dir / self.day / f"{session_name}.jsonl"

    def record(
        self,
        user_text: str,
        reply: str,
        *,
        provider: str = "",
        model: str = "",
        tokens_in: float = 0.0,
        tokens_out: float = 0.0,
        cost: float = 0.0,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        duration_s: float = 0.0,
        error: str = "",
    ) -> None:
        """Tulis satu baris JSONL berisi detail lengkap sebuah giliran."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "day": self.day,
            "session": self.session_name,
            "source": self.source,
            "user": user_text,
            "reply": reply,
            "provider": provider,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost": cost,
            "tool_calls": tool_calls or [],
            "duration_s": round(duration_s, 3),
            "error": error,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---------- statistik & bacaan ----------

    @classmethod
    def load_all(cls, history_dir: Path) -> List[Dict[str, Any]]:
        """Baca semua baris JSONL di bawah history_dir (untuk `hagema history`)."""
        entries: List[Dict[str, Any]] = []
        base = Path(history_dir)
        if not base.exists():
            return entries
        for day_dir in sorted(base.iterdir()):
            if not day_dir.is_dir():
                continue
            for f in sorted(day_dir.glob("*.jsonl")):
                try:
                    text = f.read_text(encoding="utf-8")
                except OSError:
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    @classmethod
    def summary(cls, history_dir: Path) -> str:
        """Ringkasan human-readable untuk `hagema history`."""
        entries = cls.load_all(history_dir)
        if not entries:
            return "(belum ada riwayat — mulai ngobrol dulu dengan `hagema`)"
        total_in = sum(float(e.get("tokens_in", 0) or 0) for e in entries)
        total_out = sum(float(e.get("tokens_out", 0) or 0) for e in entries)
        total_cost = sum(float(e.get("cost", 0) or 0) for e in entries)
        sessions = sorted({e.get("session", "?") for e in entries})
        lines = [
            f"Total giliran: {len(entries)}",
            f"Sesi: {len(sessions)} ({', '.join(sessions)})",
            f"Token: {total_in:,.0f} in / {total_out:,.0f} out",
            f"Estimasi biaya: ${total_cost:.4f}",
        ]
        by_source: Dict[str, int] = {}
        for e in entries:
            by_source[e.get("source", "?")] = by_source.get(e.get("source", "?"), 0) + 1
        lines.append("Sumber: " + ", ".join(f"{k}={v}" for k, v in by_source.items()))
        return "\n".join(lines)
