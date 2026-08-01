"""Lapisan provider untuk hagema-agent.

Semua provider memakai OpenAI-compatible chat completions API, sehingga
satu client tunggal menangani DeepSeek, OpenRouter, OpenAI, dan Ollama.
Termasuk klasifikasi error (quota / rate-limit / context) yang dipakai
logika failover, plus MockProvider untuk testing tanpa koneksi.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai import OpenAI


class ProviderError(Exception):
    """Kegagalan provider yang bisa direcover (quota, rate-limit, konteks)."""

    def __init__(self, kind: str, message: str, provider: str = ""):
        super().__init__(message)
        self.kind = kind        # quota | rate_limit | context | auth | api | network
        self.provider = provider

    def __repr__(self) -> str:  # pragma: no cover - hanya untuk debugging
        return f"ProviderError({self.kind!r}, {self.provider!r}, {str(self)!r})"


@dataclass
class ProviderConfig:
    """Konfigurasi statis sebuah provider."""

    name: str
    base_url: str
    model: str
    api_key_env: str = ""
    context_limit: int = 65536
    price_in: float = 0.0
    price_out: float = 0.0

    def api_key(self, env: Dict[str, str]) -> str:
        if not self.api_key_env:
            return ""
        return env.get(self.api_key_env, "")

    def has_key(self, env: Dict[str, str]) -> bool:
        return (not self.api_key_env) or bool(self.api_key(env))


class Provider:
    """Wrapper OpenAI-compatible chat completions."""

    def __init__(self, cfg: ProviderConfig, env: Dict[str, str]):
        self.cfg = cfg
        self._client = OpenAI(
            api_key=cfg.api_key(env) or "none",
            base_url=cfg.base_url,
        )

    @property
    def name(self) -> str:
        return self.cfg.name

    def list_models(self) -> List[str]:
        """Deteksi daftar model yang tersedia dari API provider (GET /models).

        Semua provider di sini OpenAI-compatible, jadi endpoint `GET {base_url}/models`
        bisa dipakai untuk mendeteksi model yang tersedia secara live.
        Melempar ProviderError bila gagal (auth, network, dll).
        """
        try:
            resp = self._client.models.list()
            return sorted(m.id for m in (resp.data or []))
        except Exception as e:  # noqa: BLE001 - klasifikasi manual di bawah
            raise _classify(e, self.name)

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.6,
    ) -> Dict[str, Any]:
        """Panggil chat completions; kembalikan dict pesan asisten.

        Melempar ProviderError untuk kegagalan yang bisa difailover.
        """
        try:
            kwargs: Dict[str, Any] = dict(
                model=self.cfg.model,
                messages=messages,
                temperature=temperature,
            )
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            resp = self._client.chat.completions.create(**kwargs)
            choices = resp.choices or []
            if not choices:
                # Provider bisa mengembalikan 200 dengan choices:null (mis. error
                # payload OpenRouter) — jangan crash dengan TypeError mentah.
                raise ProviderError(
                    "api", f"Respons tanpa choices dari provider '{self.name}'", self.name
                )
            msg = choices[0].message
            usage = getattr(resp, "usage", None)

            return {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in (msg.tool_calls or [])
                ]
                or None,
                "usage": (
                    {
                        "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                        "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    }
                    if usage
                    else None
                ),
            }
        except Exception as e:  # noqa: BLE001 - klasifikasi manual di bawah
            raise _classify(e, self.name)


class MockProvider(Provider):
    """Provider tiruan deterministik untuk dry-run & unit test (tanpa jaringan)."""

    def __init__(self, cfg: ProviderConfig, env: Optional[Dict[str, str]] = None, behavior: str = "normal"):
        self.cfg = cfg
        self.behavior = behavior
        self.calls = 0

    @property
    def name(self) -> str:
        return self.cfg.name

    def list_models(self) -> List[str]:
        """Model tiruan untuk mock (tanpa jaringan)."""
        return ["mock-model"]

    def chat(self, messages, tools=None, temperature=0.6):  # noqa: ARG002
        self.calls += 1
        if self.behavior == "quota":
            raise ProviderError("quota", "Mock: insufficient quota", self.cfg.name)
        if self.behavior == "rate_limit":
            raise ProviderError("rate_limit", "Mock: rate limited", self.cfg.name)
        if self.behavior == "context":
            raise ProviderError("context", "Mock: context length exceeded", self.cfg.name)
        if self.behavior == "auth":
            raise ProviderError("auth", "Mock: invalid api key", self.cfg.name)
        if self.behavior == "tool_call" and self.calls == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_mock_1",
                        "type": "function",
                        "function": {
                            "name": "list_directory",
                            "arguments": json.dumps({"path": "."}),
                        },
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        last = messages[-1].get("content", "") if messages else ""
        return {
            "role": "assistant",
            "content": f"Mock[{self.cfg.name}]: {str(last)[:80]}",
            "tool_calls": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }


class ProviderManager:
    """Mengelola kumpulan provider + failover order."""

    def __init__(self, config, env: Dict[str, str]):
        self.config = config
        self.env = env
        self._providers: Dict[str, Provider] = {}
        for name, cfg in config.providers.items():
            self._providers[name] = Provider(cfg, env)
        self.current_name = config.default_provider
        if self.current_name not in self._providers and self._providers:
            self.current_name = next(iter(self._providers))

    @property
    def current(self) -> Provider:
        return self._providers[self.current_name]

    def get(self, name: str) -> Optional[Provider]:
        return self._providers.get(name)

    def names(self) -> list:
        return list(self._providers.keys())

    def next_provider(self, after: str) -> Optional[Provider]:
        """Provider berikutnya dalam failover order (setelah nama `after`)."""
        order = self.config.failover_order or list(self._providers)
        try:
            idx = order.index(after)
        except ValueError:
            idx = -1
        for name in order[idx + 1:]:
            p = self._providers.get(name)
            if p is not None:
                return p
        return None


def _classify(e: Exception, provider_name: str) -> ProviderError:
    """Petakan exception openai SDK ke ProviderError dengan `kind` yang jelas."""
    from openai import APIConnectionError

    body = str(getattr(e, "body", "") or e).lower()
    status = getattr(e, "status_code", None)

    if isinstance(e, APIConnectionError):
        return ProviderError("network", str(e), provider_name)

    if status == 429 or "429" in body:
        if any(k in body for k in ("insufficient_quota", "quota", "billing", "balance")):
            return ProviderError("quota", str(e), provider_name)
        return ProviderError("rate_limit", str(e), provider_name)

    if status == 402 or any(k in body for k in ("insufficient_balance", "insufficient_quota", "402")):
        return ProviderError("quota", str(e), provider_name)

    if any(
        k in body
        for k in (
            "context_length_exceeded",
            "maximum context",
            "too many tokens",
            "token limit",
            "max context",
        )
    ):
        return ProviderError("context", str(e), provider_name)

    if status in (401, 403):
        return ProviderError("auth", str(e), provider_name)

    if status and status >= 500:
        return ProviderError("api", str(e), provider_name)

    return ProviderError("api", str(e), provider_name)
