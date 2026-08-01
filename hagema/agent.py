"""Inti agen: loop LLM + tool calling + cross-provider failover.

Failover otomatis terjadi saat provider aktif melempar ProviderError
bertipe quota / rate_limit / context: sesi di-REKAP dengan provider
berikutnya, ringkasan disuntikkan ke konteks, lalu percakapan berlanjut
dengan provider baru tanpa kehilangan konteks.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .memory import Memory
from .providers import ProviderError, ProviderManager
from .session import Session
from .skills import SkillsRegistry
from .tools import TOOLS_SCHEMA, ToolExecutor

SYSTEM_TEMPLATE = """Kamu adalah hagema-agent, agen AI otonom bergaya Hermes Agent (Nous Research).

Kamu punya tools: run_terminal, read_file, write_file, list_directory, load_skill.
Aturan:
- Selesaikan tugas user dengan memanggil tools. JANGAN menebak hasil perintah terminal — jalankan dulu.
- Selalu baca file sebelum mengubahnya.
- Gunakan bahasa yang sama dengan user (default: Bahasa Indonesia).
- Setelah menyelesaikan prosedur non-trivial, jelaskan singkat langkahnya agar bisa dijadikan skill.
"""

FAILOVER_REASON = {
    "quota": "kuota/balance habis",
    "rate_limit": "rate limit tercapai",
    "context": "konteks penuh",
}


class Agent:
    def __init__(
        self,
        config: Any,
        provider_mgr: ProviderManager,
        session: Session,
        executor: ToolExecutor,
        skills: SkillsRegistry,
        memory: Memory,
    ):
        self.config = config
        self.provider_mgr = provider_mgr
        self.session = session
        self.executor = executor
        self.skills = skills
        self.memory = memory
        self.usage: Dict[str, Dict[str, float]] = {}

    # ---------- prompt ----------

    @property
    def provider(self):
        return self.provider_mgr.current

    def system_prompt(self) -> str:
        parts = [SYSTEM_TEMPLATE]
        mem = self.memory.load()
        if mem:
            parts.append(f"## MEMORI JANGKA PANJANG (MEMORY.md)\n{mem}")
        parts.append(f"## SKILL TERSEDIA\n{self.skills.index_text()}")
        if self.session.recap:
            parts.append(
                f"## REKAP SESI SEBELUMNYA (dari provider lain — lanjutkan dari sini)\n{self.session.recap}"
            )
        return "\n\n".join(parts)

    # ---------- loop utama ----------

    def run(self, user_text: str) -> str:
        self.session.add({"role": "user", "content": user_text})
        return self._loop()

    def _loop(self, retries: int = 2) -> str:
        for _ in range(12):  # batas iterasi tool
            try:
                msgs = self.session.chat_messages(
                    self.system_prompt(), max_context=self.provider.cfg.context_limit
                )
                result = self.provider.chat(msgs, tools=TOOLS_SCHEMA)
            except ProviderError as err:
                if retries > 0 and self._failover(err):
                    return self._loop(retries - 1)
                return f"[ERROR dari provider '{err.provider}'] {err}"

            self._track_usage(result)
            self.session.add({k: v for k, v in result.items() if k != "usage"})

            tool_calls = result.get("tool_calls")
            if not tool_calls:
                return (result.get("content") or "").strip() or "(tanpa konten)"

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                output = self.executor.execute(name, args)
                self.session.add({"role": "tool", "tool_call_id": tc.get("id"), "content": output})

        return "ERROR: batas iterasi tool terlampaui."

    # ---------- failover ----------

    def _failover(self, err: ProviderError) -> bool:
        """Rekap sesi lalu pindah ke provider berikutnya. True jika berhasil."""
        if err.kind not in FAILOVER_REASON:
            return False
        nxt = self.provider_mgr.next_provider(after=self.provider.name)
        if nxt is None:
            return False
        old_name = self.provider.name
        reason = FAILOVER_REASON[err.kind]
        recap = self.session.generate_recap(nxt)  # rekap pakai provider tujuan
        self.provider_mgr.current_name = nxt.name
        if recap:
            self.session.compress_tail(4)
        self.session.add(
            {
                "role": "system",
                "content": f"[FAILOVER] Provider '{old_name}' gagal ({reason}). "
                f"Sesi direkap & dilanjutkan dengan '{nxt.name}'. Lanjutkan tugas user.",
            }
        )
        return True

    # ---------- usage ----------

    def _track_usage(self, result: Dict[str, Any]) -> None:
        u = result.get("usage") or {}
        name = self.provider.name
        stats = self.usage.setdefault(name, {"input_tokens": 0.0, "output_tokens": 0.0, "cost": 0.0})
        inp = float(u.get("input_tokens", 0) or 0)
        out = float(u.get("output_tokens", 0) or 0)
        stats["input_tokens"] += inp
        stats["output_tokens"] += out
        stats["cost"] += (
            inp / 1_000_000 * self.provider.cfg.price_in
            + out / 1_000_000 * self.provider.cfg.price_out
        )

    def usage_text(self) -> str:
        if not self.usage:
            return "(belum ada penggunaan)"
        lines = []
        total = 0.0
        for name, s in self.usage.items():
            lines.append(
                f"  {name}: {s['input_tokens']:,.0f} in / {s['output_tokens']:,.0f} out — ${s['cost']:.6f}"
            )
            total += s["cost"]
        lines.append(f"  TOTAL estimasi: ${total:.6f}")
        return "\n".join(lines)
