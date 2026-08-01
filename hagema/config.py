"""Konfigurasi untuk hagema-agent.

Membaca config.yaml (provider, failover order, lokasi penyimpanan)
dan memuat file .env ke environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

import yaml

from .providers import ProviderConfig

DEFAULT_CONFIG_PATH = Path.home() / ".hagema" / "config.yaml"


def _expand(path: str) -> Path:
    """Expand ~ dan variabel env di sebuah path string."""
    return Path(os.path.expanduser(os.path.expandvars(path)))


class Config:
    """Config wrapper yang sudah diparse dari YAML."""

    def __init__(self, raw: Dict):
        self.raw = raw
        self.default_provider = raw.get("default_provider", "deepseek")
        self.failover_order: list = list(raw.get("failover_order") or [])
        self.providers: Dict[str, ProviderConfig] = {}

        for name, p in (raw.get("providers") or {}).items():
            self.providers[name] = ProviderConfig(
                name=name,
                base_url=p.get("base_url", ""),
                model=p.get("model", ""),
                api_key_env=p.get("api_key_env", ""),
                context_limit=int(p.get("context_limit", 65536)),
                price_in=float(p.get("price_per_1m_input", 0.0)),
                price_out=float(p.get("price_per_1m_output", 0.0)),
            )

        self.sessions_dir = _expand(raw.get("sessions_dir", "~/.hagema/sessions"))
        self.skills_dir = _expand(raw.get("skills_dir", "~/.hagema/skills"))
        self.memory_file = _expand(raw.get("memory_file", "~/.hagema/MEMORY.md"))

        # --- akses jarak jauh (server desktop / web / telegram) ---
        web = raw.get("web") or {}
        self.web_enabled = bool(web.get("enabled", False))
        self.web_host = str(web.get("host", "127.0.0.1"))
        self.web_port = int(web.get("port", 8765))
        self.web_token = str(web.get("token", "") or "")

        tg = raw.get("telegram") or {}
        self.tg_enabled = bool(tg.get("enabled", False))
        self.tg_token = str(tg.get("token", "") or "")
        self.tg_allow: list = [int(x) for x in (tg.get("allow") or []) if str(x).strip().isdigit()]

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            raise FileNotFoundError(f"Config tidak ditemukan: {path}")
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return cls(raw)

    def load_env(self, env_file: Path) -> None:
        """Muat file .env ke os.environ (hanya key yang belum terisi)."""
        if not env_file.exists():
            return
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)

    def provider_names(self) -> list:
        return list(self.providers.keys())

    # ---------- menulis config kembali ----------

    def save(self, path: Path) -> None:
        """Tulis ulang config ke file (dipakai oleh setup & perintah model)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.raw, f, allow_unicode=True, sort_keys=False)

    def set_default_provider(self, name: str) -> None:
        """Ubah provider default di dalam raw config."""
        self.default_provider = name
        self.raw["default_provider"] = name

    def set_model(self, name: str, model: str) -> None:
        """Ubah model untuk sebuah provider di dalam raw config."""
        if name not in self.providers:
            raise KeyError(f"Provider '{name}' tidak ada")
        self.providers[name].model = model
        self.raw.setdefault("providers", {}).setdefault(name, {})["model"] = model
