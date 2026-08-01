"""Antarmuka CLI untuk hagema-agent — gaya opencode.

Subcommand:
  hagema                     → chat REPL (default)
  hagema setup               → wizard konfigurasi interaktif
  hagema model               → lihat/ganti provider aktif
  hagema doctor              → periksa instalasi & konfigurasi
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from . import __version__
from .agent import Agent
from .config import DEFAULT_CONFIG_PATH, Config
from .memory import Memory
from .providers import MockProvider, ProviderConfig, ProviderManager
from .session import Session
from .skills import SkillsRegistry
from .tools import ToolExecutor

BANNER = (
    "[bold cyan]hagema-agent[/bold cyan] v" + __version__ + " — agen AI otonom ala Hermes Agent\n"
    "[dim]cross-provider failover: token/provider habis → sesi otomatis direkap & dilanjutkan[/dim]"
)

HELP = """**Slash commands:**
- `/help` — bantuan ini
- `/providers` — daftar provider + status key
- `/models` — deteksi & daftar model yang tersedia di provider aktif
- `/switch <nama>` — rekap sesi lalu pindah provider
- `/recap` — buat rekap sesi sekarang (tanpa pindah provider)
- `/usage` — token & estimasi biaya per provider
- `/skills` — daftar skill terpasang
- `/skill <nama>` — muat skill ke konteks
- `/memory` — tampilkan MEMORY.md
- `/remember <teks>` — simpan catatan ke MEMORY.md
- `/clear` — bersihkan riwayat sesi
- `/exit` — keluar
"""

DEFAULT_PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "context_limit": 65536,
        "price_in": 0.27,
        "price_out": 1.10,
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat-v3-0324",
        "api_key_env": "OPENROUTER_API_KEY",
        "context_limit": 128000,
        "price_in": 0.25,
        "price_out": 0.35,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "context_limit": 128000,
        "price_in": 0.15,
        "price_out": 0.60,
    },
    "ollama": {
        "label": "Ollama (lokal)",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3:32b",
        "api_key_env": "",
        "context_limit": 65536,
        "price_in": 0.0,
        "price_out": 0.0,
    },
}

DEFAULT_FAILOVER = ["deepseek", "openrouter", "openai", "ollama"]


# ============================================================
# Komponen inti
# ============================================================


def build_components(args) -> tuple:
    cfg_path = Path(args.config)
    if cfg_path.exists():
        cfg = Config.load(cfg_path)
    else:
        # Fallback default (dipakai saat mode mock tanpa config)
        cfg = Config({"default_provider": "mock-a", "failover_order": ["mock-a", "mock-b"], "providers": {}})
    cfg.load_env(Path(args.env))

    if args.mock:
        pm = build_mock_manager(args.mock)
    else:
        pm = ProviderManager(cfg, os.environ)

    session = Session.create(cfg.sessions_dir, args.session)
    skills = SkillsRegistry(cfg.skills_dir)
    memory = Memory(cfg.memory_file)
    executor = ToolExecutor(
        confirm=(
            (lambda _cmd: True)
            if args.yes
            else (lambda cmd: Confirm.ask(f"Jalankan perintah? {cmd}", default=True))
        ),
        cwd=Path(args.cwd),
        skills=skills,
    )
    agent = Agent(cfg, pm, session, executor, skills, memory)
    return cfg, pm, session, skills, memory, agent


def build_mock_manager(behavior: str) -> ProviderManager:
    """Manager dengan MockProvider untuk dry-run / demo tanpa API key."""
    cfg = Config(
        {
            "default_provider": "mock-a",
            "failover_order": ["mock-a", "mock-b"],
            "providers": {},
        }
    )

    def mk(name):
        return ProviderConfig(name=name, base_url="http://127.0.0.1:1/v1", model="mock-model")

    pm = ProviderManager.__new__(ProviderManager)
    pm.config = cfg
    pm.env = {}
    pm._providers = {
        "mock-a": MockProvider(mk("mock-a"), behavior=behavior),
        "mock-b": MockProvider(mk("mock-b"), behavior="normal"),
    }
    pm.current_name = "mock-a"
    return pm


# ============================================================
# Subcommand: chat (default)
# ============================================================


def cmd_chat(args, console) -> int:
    if args.mock:
        cfg, pm, session, skills, memory, agent = build_components(args)
    else:
        cfg_path = Path(args.config)
        if not cfg_path.exists():
            console.print(f"[red]Config tidak ditemukan: {cfg_path}[/red]")
            console.print("Jalankan dulu: [bold]hagema setup[/bold]")
            return 1
        cfg, pm, session, skills, memory, agent = build_components(args)

    console.print(Panel(BANNER, border_style="cyan"))
    console.print(f"Sesi: [bold]{session.path}[/bold]")
    if args.mock:
        console.print(f"[yellow]MODE MOCK ({args.mock}) — tidak ada API key yang dipakai.[/yellow]")
    console.print("Ketik /help untuk bantuan. Ctrl+C untuk keluar.\n")

    ctx = (cfg, pm, session, skills, memory, agent, console)
    while True:
        try:
            user_input = Prompt.ask("[bold green]kamu[/bold green]")
        except (EOFError, KeyboardInterrupt):
            console.print("\nSampai jumpa! 👋")
            return 0
        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.startswith("/"):
            if not handle_command(user_input, ctx):
                return 0
            continue
        try:
            with console.status("[cyan]memikirkan & bekerja...[/cyan]"):
                reply = agent.run(user_input)
        except KeyboardInterrupt:
            console.print("\n[dimm]dibatalkan[/dimm]")
            continue
        console.print(Markdown(reply))


def handle_command(line: str, ctx) -> bool:
    """Proses slash command; kembalikan False jika harus keluar."""
    cfg, pm, session, skills, memory, agent, console = ctx
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/exit":
        console.print("Sampai jumpa! 👋")
        return False
    if cmd == "/help":
        console.print(Markdown(HELP))
        return True
    if cmd == "/providers":
        console.print("[bold]Provider tersedia:[/bold]")
        for name, p in pm._providers.items():
            marker = "★ aktif" if name == pm.current_name else "   "
            key_ok = p.cfg.has_key(os.environ)
            key_state = "key OK" if key_ok else "key KOSONG"
            console.print(f"  {marker} {name} → {p.cfg.model} [{key_state}]")
        return True
    if cmd == "/models":
        if not pm.current.cfg.has_key(os.environ):
            console.print(
                f"[yellow]⚠ Provider '{pm.current_name}' belum punya API key "
                f"(isi di .env: {pm.current.cfg.api_key_env or '(tidak butuh key)'}). "
                f"Jalankan [bold]hagema setup[/bold].[/yellow]"
            )
            return True
        try:
            models = pm.current.list_models()
        except Exception as e:  # noqa: BLE001 - tampilkan pesan mentah
            console.print(f"[red]Gagal mendeteksi model: {e}[/red]")
            return True
        current = pm.current.cfg.model
        console.print(f"[bold]Model tersedia ({pm.current_name}):[/bold]")
        for i, m in enumerate(models, 1):
            marker = "★ aktif" if m == current else "   "
            console.print(f"  {marker} {m}")
            if i >= 30:
                console.print(f"  … dan {len(models) - i} lainnya (total {len(models)})")
                break
        console.print(f"Ganti model: [bold]hagema model {pm.current_name} <nama-model>[/bold]")
        return True
    if cmd == "/switch":
        if not arg:
            console.print("Pakai: /switch <nama-provider>")
            return True
        target = pm.get(arg)
        if target is None:
            console.print(f"Provider '{arg}' tidak ada. Tersedia: {', '.join(pm.names())}")
            return True
        if not target.cfg.has_key(os.environ):
            console.print(
                f"[yellow]⚠ Perhatian: provider '{arg}' belum punya API key "
                f"(isi di .env: {target.cfg.api_key_env or '(tidak butuh key)'}). "
                f"Chat berikutnya bisa gagal.[/yellow]"
            )
        old = pm.current_name
        recap = session.generate_recap(pm.current)
        if recap:
            session.compress_tail(4)
        pm.current_name = arg
        console.print(f"Provider: {old} → {arg} (recap: {'✓' if recap else '✗'})")
        return True
    if cmd == "/recap":
        recap = session.generate_recap(pm.current)
        if recap:
            console.print(Markdown(f"## Recap sesi\n\n{recap}"))
        else:
            console.print("Belum ada percakapan untuk direkap.")
        return True
    if cmd == "/usage":
        console.print(agent.usage_text())
        return True
    if cmd == "/skills":
        skills.refresh()
        console.print("[bold]Skill terpasang:[/bold]")
        console.print(skills.index_text())
        return True
    if cmd == "/skill":
        skill = skills.get(arg)
        if skill is None:
            console.print(f"Skill '{arg}' tidak ditemukan.")
            return True
        md = skill.load_markdown()
        session.add({"role": "system", "content": f"Skill '{skill.name}' dimuat:\n{md}"})
        console.print(f"Skill '{skill.name}' dimuat ke konteks ✓")
        return True
    if cmd == "/memory":
        mem = memory.load()
        console.print("[bold]MEMORY.md:[/bold]")
        console.print(mem if mem else "(kosong)")
        return True
    if cmd == "/remember":
        if not arg:
            console.print("Pakai: /remember <teks>")
            return True
        console.print(f"Disimpan: {memory.append(arg)}")
        return True
    if cmd == "/clear":
        session.messages = []
        session.recap = None
        session.save()
        console.print("Sesi dibersihkan.")
        return True
    console.print(f"Perintah tidak dikenal: {cmd}. Ketik /help")
    return True


# ============================================================
# Subcommand: setup (wizard interaktif)
# ============================================================


def cmd_setup(args, console) -> int:
    cfg_path = Path(args.config)
    env_path = Path(args.env)

    console.print(Panel(f"[bold cyan]hagema setup[/bold cyan] v{__version__}", border_style="cyan"))
    console.print(
        "Wizard ini menulis [bold]config.yaml[/bold] dan [bold].env[/bold] otomatis.\n"
        "Model & provider diatur di config — hanya API key yang disimpan di .env.\n"
    )

    raw = {"providers": {}, "failover_order": []}
    env_lines = {}

    # Pilih provider
    console.print("[bold]Provider tersedia:[/bold]")
    names = list(DEFAULT_PROVIDERS.keys())
    for i, name in enumerate(names, 1):
        d = DEFAULT_PROVIDERS[name]
        console.print(f"  {i}. {name} — {d['label']} ({d['model']})")
    choice = Prompt.ask("Pilih nomor (pisahkan koma, contoh: 1,3) atau Enter untuk semua", default="semua")
    if choice.strip().lower() not in ("", "semua", "all"):
        try:
            indices = [int(x.strip()) for x in choice.split(",") if x.strip().isdigit()]
            names = [names[i - 1] for i in indices if 1 <= i <= len(names)]
        except (ValueError, IndexError):
            console.print("[yellow]Input tidak valid, pakai semua provider.[/yellow]")
            names = list(DEFAULT_PROVIDERS.keys())
    if not names:
        names = list(DEFAULT_PROVIDERS.keys())

    for name in names:
        d = DEFAULT_PROVIDERS[name]
        console.print(f"\n[bold cyan]→ {name}[/bold cyan]")
        model = Prompt.ask(f"  Model (Enter = {d['model']})", default=d["model"]) or d["model"]
        if d["api_key_env"]:
            key = Prompt.ask(
                f"  API key {name} (bisa dikosongkan; atau isi langsung)",
                default="",
            )
            if key.strip():
                env_lines[d["api_key_env"]] = key.strip()
        else:
            console.print("  (lokal, tidak butuh API key)")
        raw["providers"][name] = {
            "base_url": d["base_url"],
            "model": model,
            "api_key_env": d["api_key_env"],
            "context_limit": d["context_limit"],
            "price_per_1m_input": d["price_in"],
            "price_per_1m_output": d["price_out"],
        }

    # Default provider
    default = Prompt.ask("Provider default", default=names[0]) or names[0]
    if default not in raw["providers"]:
        default = names[0]
    raw["default_provider"] = default
    raw["failover_order"] = [n for n in DEFAULT_FAILOVER if n in raw["providers"]]
    raw["sessions_dir"] = str(cfg_path.parent / "sessions")
    raw["skills_dir"] = str(cfg_path.parent / "skills")
    raw["memory_file"] = str(cfg_path.parent / "MEMORY.md")

    # ---- Akses jarak jauh (opsional) ----
    console.print("\n[bold cyan]Akses dari HP (opsional):[/bold cyan]")
    if Confirm.ask("Aktifkan web controller untuk [bold]hagema server[/bold]?", default=False):
        web_host = Prompt.ask("  Web host", default="0.0.0.0")
        web_port = Prompt.ask("  Web port", default="8765")
        web_token = Prompt.ask("  Token akses web (kosongkan = tanpa token)", default="")
        raw["web"] = {
            "enabled": True,
            "host": web_host,
            "port": int(web_port) if str(web_port).isdigit() else 8765,
            "token": web_token,
        }
    if Confirm.ask("Aktifkan Telegram bot untuk [bold]hagema server[/bold]?", default=False):
        tg_token = Prompt.ask("  Token bot Telegram (dari @BotFather)", default="")
        tg_allow = Prompt.ask("  chat_id yang diizinkan (pisahkan koma, boleh kosong dulu)", default="")
        raw["telegram"] = {
            "enabled": bool(tg_token),
            "token": tg_token,
            "allow": [int(x) for x in tg_allow.split(",") if x.strip().isdigit()],
        }

    # Tulis file
    cfg = Config(raw)
    cfg.save(cfg_path)

    if env_lines:
        existing = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()
        existing.update(env_lines)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(
            "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n", encoding="utf-8"
        )
        console.print(f"[green]API key disimpan di {env_path}[/green]")

    console.print(f"[green]Config ditulis ke {cfg_path}[/green]")
    console.print("\nSelesai! Jalankan [bold]hagema[/bold] untuk mulai ngobrol. 🚀")
    return 0


# ============================================================
# Subcommand: model
# ============================================================


def cmd_model(args, console) -> int:
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        console.print(f"[red]Config tidak ditemukan: {cfg_path}[/red]")
        console.print("Jalankan dulu: [bold]hagema setup[/bold]")
        return 1
    cfg = Config.load(cfg_path)
    cfg.load_env(Path(args.env))

    if args.name:
        # ganti default provider (dan opsional ganti model)
        if args.name not in cfg.providers:
            console.print(f"[red]Provider '{args.name}' tidak ada. Tersedia: {', '.join(cfg.provider_names())}[/red]")
            return 1
        if args.model:
            cfg.set_model(args.name, args.model)
            console.print(f"Model {args.name} → [bold]{args.model}[/bold] ✓")
        cfg.set_default_provider(args.name)
        cfg.save(cfg_path)
        console.print(f"Provider default → [bold]{args.name}[/bold] ✓")
        return 0

    # tampilkan daftar
    console.print("[bold]Provider terkonfigurasi:[/bold]")
    pm = ProviderManager(cfg, os.environ)
    for name, p in pm._providers.items():
        marker = "★ aktif" if name == pm.current_name else "   "
        key_ok = p.cfg.has_key(os.environ)
        key_state = "key OK" if key_ok else "key KOSONG"
        console.print(f"  {marker} {name} → {p.cfg.model} [{key_state}]")
    console.print("\nGanti default: [bold]hagema model <nama>[/bold]")
    console.print("Ganti model: [bold]hagema model <nama> <nama-model>[/bold]")
    console.print("Deteksi model: [bold]hagema models [nama-provider][/bold]")
    return 0


def cmd_models(args, console) -> int:
    """Deteksi & tampilkan model yang tersedia dari API provider (GET /models)."""
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        console.print(f"[red]Config tidak ditemukan: {cfg_path}[/red]")
        console.print("Jalankan dulu: [bold]hagema setup[/bold]")
        return 1
    cfg = Config.load(cfg_path)
    cfg.load_env(Path(args.env))
    pm = ProviderManager(cfg, os.environ)

    names = [args.provider] if args.provider else [pm.current_name]
    failed = False
    for name in names:
        p = pm.get(name)
        if p is None:
            console.print(f"[red]Provider '{name}' tidak ada. Tersedia: {', '.join(pm.names())}[/red]")
            failed = True
            continue
        if not p.cfg.has_key(os.environ):
            console.print(
                f"[yellow]⚠ Provider '{name}' belum punya API key "
                f"(isi di .env: {p.cfg.api_key_env or '(tidak butuh key)'}). "
                f"Jalankan [bold]hagema setup[/bold].[/yellow]"
            )
            failed = True
            continue
        current = p.cfg.model
        console.print(f"[bold]Mendeteksi model untuk {name}...[/bold]")
        try:
            models = p.list_models()
        except Exception as e:  # noqa: BLE001 - tampilkan pesan mentah
            console.print(f"[red]  Gagal: {e}[/red]")
            failed = True
            continue
        if not models:
            console.print("  (tidak ada model terdeteksi)")
            failed = True
            continue
        console.print(f"  {len(models)} model ditemukan:")
        for i, m in enumerate(models, 1):
            marker = "★ aktif" if m == current else "   "
            console.print(f"  {marker} {m}")
            if i >= 30:
                console.print(f"  … dan {len(models) - i} lainnya (total {len(models)})")
                break
        console.print(f"Ganti: [bold]hagema model {name} <nama-model>[/bold]\n")
    return 1 if failed else 0


# ============================================================
# Subcommand: doctor
# ============================================================


def cmd_doctor(args, console) -> int:
    cfg_path = Path(args.config)
    console.print(Panel("[bold cyan]hagema doctor[/bold cyan]", border_style="cyan"))
    console.print(f"Versi: [bold]{__version__}[/bold]")
    console.print(f"Config: [bold]{cfg_path}[/bold] {'✓ ada' if cfg_path.exists() else '✗ belum ada'}")

    if not cfg_path.exists():
        console.print("Jalankan [bold]hagema setup[/bold] untuk membuat config.")
        return 0

    cfg = Config.load(cfg_path)
    cfg.load_env(Path(args.env))
    pm = ProviderManager(cfg, os.environ)
    console.print("[bold]Provider:[/bold]")
    for name, p in pm._providers.items():
        key_ok = p.cfg.has_key(os.environ)
        key_state = "key OK" if key_ok else "key KOSONG"
        console.print(f"  ✓ {name} → {p.cfg.model} [{key_state}]")

    skills_dir = cfg.skills_dir
    console.print(f"Skills: [bold]{skills_dir}[/bold] ({'ada' if skills_dir.exists() else 'belum ada'})")
    sessions_dir = cfg.sessions_dir
    console.print(f"Sessions: [bold]{sessions_dir}[/bold] ({'ada' if sessions_dir.exists() else 'belum ada'})")

    console.print("[bold]Akses jarak jauh:[/bold]")
    if cfg.web_enabled:
        token_state = "token 🔒" if cfg.web_token else "tanpa token"
        console.print(f"  ✓ Web controller: {cfg.web_host}:{cfg.web_port} [{token_state}]")
    else:
        console.print("  ✗ Web controller: nonaktif (atur di `hagema setup`)")
    if cfg.tg_enabled:
        console.print(f"  ✓ Telegram bot: aktif (izinkan: {cfg.tg_allow or 'belum ada'})")
    else:
        console.print("  ✗ Telegram bot: nonaktif (atur di `hagema setup`)")
    console.print("\nDoctor selesai ✓")
    return 0


# ============================================================
# Entry point
# ============================================================


def make_common_parser() -> argparse.ArgumentParser:
    """Parser bersama berisi --config dan --env (dipakai di semua subcommand)."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Path config.yaml (default: {DEFAULT_CONFIG_PATH})",
    )
    common.add_argument(
        "--env",
        default=str(DEFAULT_CONFIG_PATH.parent / ".env"),
        help=f"Path file .env (default: {DEFAULT_CONFIG_PATH.parent / '.env'})",
    )
    return common


def _server_flag_default(args, kind: str) -> bool:
    """Default on/off untuk server dari config (kalau tidak diberi flag eksplisit)."""
    try:
        from .config import Config
        cfg_path = Path(args.config)
        if cfg_path.exists():
            cfg = Config.load(cfg_path)
            if kind == "web":
                return cfg.web_enabled
            return cfg.tg_enabled
    except Exception:  # noqa: BLE001 - default aman
        pass
    return False


def main(argv=None) -> int:
    common = make_common_parser()
    parser = argparse.ArgumentParser(
        prog="hagema",
        description="Agen AI otonom ala Hermes Agent — cross-provider failover dengan rekap otomatis",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"hagema-agent {__version__}")
    # flag chat (berlaku juga di root tanpa subcommand)
    parser.add_argument("--session", default=None, help="Nama sesi (default: auto)")
    parser.add_argument("--cwd", default=os.getcwd(), help="Working directory untuk tools")
    parser.add_argument("--yes", action="store_true", help="Auto-approve semua perintah terminal")
    parser.add_argument("--mock", nargs="?", const="normal",
                        choices=["normal", "quota", "rate_limit", "context", "tool_call"],
                        help="Gunakan MockProvider (tanpa API key). 'quota' untuk demo failover.")

    sub = parser.add_subparsers(dest="command")

    # setup
    sub.add_parser("setup", parents=[make_common_parser()],
                   help="Wizard konfigurasi interaktif (provider, model, API key)")

    # model
    p_model = sub.add_parser("model", parents=[make_common_parser()],
                             help="Lihat / ganti provider default atau model")
    p_model.add_argument("name", nargs="?", help="Nama provider yang dijadikan default")
    p_model.add_argument("model", nargs="?", help="Opsional: model baru untuk provider itu")

    # models (deteksi model dari API)
    p_models = sub.add_parser("models", parents=[make_common_parser()],
                              help="Deteksi daftar model yang tersedia dari API provider")
    p_models.add_argument("provider", nargs="?", help="Nama provider (default: provider aktif)")

    # doctor
    sub.add_parser("doctor", parents=[make_common_parser()],
                   help="Periksa instalasi & konfigurasi")

    # serve (web app untuk kontrol dari HP/browser)
    p_serve = sub.add_parser("serve", parents=[make_common_parser()],
                             help="Jalankan web app lokal untuk kontrol dari HP")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1; gunakan 0.0.0.0 untuk LAN)")
    p_serve.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    p_serve.add_argument("--token", default=None, help="Opsional: token akses Bearer")
    p_serve.add_argument("--session", default=None, help="Nama sesi (default: web)")
    p_serve.add_argument("--cwd", default=os.getcwd(), help="Working directory untuk tools")
    p_serve.add_argument("--yes", action="store_true", help="Auto-approve perintah terminal")

    # telegram (bot untuk kontrol dari HP)
    p_tg = sub.add_parser("telegram", parents=[make_common_parser()],
                          help="Jalankan Telegram bot untuk kontrol dari HP")
    p_tg.add_argument("--token", default=None, help="Token bot Telegram (atau env HAGEMA_TELEGRAM_TOKEN)")
    p_tg.add_argument("--allow", default=None, help="Daftar chat_id yang diizinkan, pisahkan koma (contoh: 123,456)")
    p_tg.add_argument("--session", default=None, help="Nama sesi (default: telegram)")
    p_tg.add_argument("--cwd", default=os.getcwd(), help="Working directory untuk tools")
    p_tg.add_argument("--yes", action="store_true", help="Auto-approve perintah terminal")

    # server (mode desktop: dashboard monitoring + controller)
    p_srv = sub.add_parser("server", parents=[make_common_parser()],
                           help="Mode server: dashboard monitoring + web/Telegram controller")
    p_srv.add_argument("--web", action="store_true", help="Aktifkan web controller (default: dari config)")
    p_srv.add_argument("--no-web", action="store_true", help="Matikan web controller")
    p_srv.add_argument("--host", default=None, help="Bind address web (default: dari config)")
    p_srv.add_argument("--port", type=int, default=None, help="Port web (default: dari config)")
    p_srv.add_argument("--token", default=None, help="Token akses web (default: dari config)")
    p_srv.add_argument("--telegram", action="store_true", help="Aktifkan Telegram bot (default: dari config)")
    p_srv.add_argument("--no-telegram", action="store_true", help="Matikan Telegram bot")
    p_srv.add_argument("--tg-token", default=None, help="Token bot Telegram (default: dari config/env)")
    p_srv.add_argument("--allow", default=None, help="Daftar chat_id Telegram yang diizinkan, pisahkan koma")
    p_srv.add_argument("--session", default=None, help="Nama sesi (default: server)")
    p_srv.add_argument("--cwd", default=os.getcwd(), help="Working directory untuk tools")
    p_srv.add_argument("--yes", action="store_true", help="Auto-approve perintah terminal")

    args = parser.parse_args(argv)
    console = Console()

    if args.command == "setup":
        return cmd_setup(args, console)
    if args.command == "model":
        return cmd_model(args, console)
    if args.command == "models":
        return cmd_models(args, console)
    if args.command == "doctor":
        return cmd_doctor(args, console)
    if args.command == "serve":
        from .web import run_web
        return run_web(args, console)
    if args.command == "telegram":
        from .telegram import run_telegram
        return run_telegram(args, console)
    if args.command == "server":
        from .server import run_server
        # tentukan web/telegram dari flag; fallback ke config
        args.web = not args.no_web and (args.web or _server_flag_default(args, "web"))
        args.telegram = not args.no_telegram and (args.telegram or _server_flag_default(args, "telegram"))
        return run_server(args, console)
    return cmd_chat(args, console)


if __name__ == "__main__":
    sys.exit(main())
