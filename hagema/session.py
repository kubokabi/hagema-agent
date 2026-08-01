"""Sesi percakapan + logika REKAP.

Sesi disimpan sebagai JSONL (satu pesan per baris) agar mudah di-append,
dipulihkan, dan dipindah. Inti fitur: `generate_recap()` meringkas sesi
menjadi markdown terstruktur yang bisa disuntikkan ke provider lain saat
failover.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

RECAP_SYSTEM_PROMPT = """Kamu adalah mesin rekap sesi agen AI. Buat ringkasan (recap) percakapan
di bawah ini dalam Bahasa Indonesia, format Markdown dengan bagian:
## Konteks / Tujuan
## Yang sudah dikerjakan
## Keputusan & temuan penting
## File / kode yang dibuat atau diubah
## Langkah selanjutnya

Ringkas tapi padat informasi, maksimal 600 kata. Fokus pada detail yang
dibutuhkan agen lain untuk MELANJUTKAN pekerjaan tanpa percakapan asli.
Jangan sertakan prompt di luar yang relevan."""


class Session:
    """Kumpulan pesan yang dipersist ke JSONL + fasilitas rekap."""

    def __init__(self, path: Path):
        self.path = path
        self.messages: List[Dict[str, Any]] = []
        self.recap: Optional[str] = None
        if path.exists():
            self._load()

    # ---------- konstruksi / persistensi ----------

    @classmethod
    def create(cls, sessions_dir: Path, name: Optional[str] = None) -> "Session":
        sessions_dir.mkdir(parents=True, exist_ok=True)
        if not name:
            name = time.strftime("session-%Y%m%d-%H%M%S")
        return cls(sessions_dir / f"{name}.jsonl")

    def _load(self) -> None:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                self.messages.append(msg)
        self._load_recap()

    # ---------- persistensi recap ----------

    @property
    def recap_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".recap.md")

    def _load_recap(self) -> None:
        if self.recap_path.exists():
            self.recap = self.recap_path.read_text(encoding="utf-8")

    def _persist_recap(self) -> None:
        if self.recap:
            self.recap_path.write_text(self.recap, encoding="utf-8")
        elif self.recap_path.exists():
            self.recap_path.unlink()

    def save(self) -> None:
        """Tulis ulang seluruh file JSONL dari self.messages (dipakai saat kompresi)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            for m in self.messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        self._persist_recap()

    def add(self, message: Dict[str, Any]) -> None:
        message = dict(message)
        message.setdefault("ts", time.time())
        self.messages.append(message)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    # ---------- transformasi untuk API ----------

    def chat_messages(self, system_prompt: str, max_context: Optional[int] = None) -> List[Dict[str, Any]]:
        """Pesan siap kirim ke provider: system prompt + riwayat (tanpa ts)."""
        msgs: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for m in self.messages:
            if m.get("role") == "system":
                msgs.append({"role": "system", "content": str(m.get("content", ""))})
                continue
            msgs.append(
                {
                    k: v
                    for k, v in m.items()
                    if k in ("role", "content", "tool_calls", "tool_call_id") and v is not None
                }
            )
        # Pengaman kasar: kalau estimasi token melebihi batas, pakai ekor pesan.
        if max_context:
            est = sum(len(str(m.get("content") or "")) for m in msgs) // 4
            if est > max_context:
                tail = self._tail(10)
                msgs = [{"role": "system", "content": system_prompt}]
                if self.recap:
                    msgs.append({"role": "system", "content": f"REKAP SESI:\n{self.recap}"})
                msgs.extend(tail)
        return msgs

    def _tail(self, n: int = 10) -> List[Dict[str, Any]]:
        """n pesan user/assistant terakhir (skip pesan tool & tool_calls murni)."""
        kept: List[Dict[str, Any]] = []
        for m in reversed(self.messages):
            role = m.get("role")
            if role not in ("user", "assistant"):
                continue
            if role == "assistant" and not m.get("content"):
                continue  # pesan asisten yang hanya berisi tool_calls
            kept.append({k: v for k, v in m.items() if k in ("role", "content")})
            if len(kept) >= n:
                break
        return list(reversed(kept))

    def compress_tail(self, n: int = 6) -> None:
        """Pangkas riwayat sesi menjadi n pesan terakhir (dipakai setelah rekap).

        Menulis ulang file JSONL agar kompresi tetap berlaku setelah restart.
        """
        self.messages = self._tail(n)
        self.save()

    # ---------- rekap ----------

    def generate_recap(self, provider, max_messages: int = 20) -> str:
        """Rekap sesi memakai provider yang diberikan; kembalikan teks rekap."""
        tail = self._tail(max_messages)
        if not tail:
            return ""
        msgs: List[Dict[str, Any]] = [
            {"role": "system", "content": RECAP_SYSTEM_PROMPT},
            *tail,
            {"role": "user", "content": "Rekap sesi ini."},
        ]
        try:
            result = provider.chat(msgs, temperature=0.2)
        except Exception:  # noqa: BLE001 - rekap tidak boleh mematikan failover
            return ""
        content = (result.get("content") or "").strip()
        if content:
            self.recap = content
            self._persist_recap()
        return content
