"""Mode server desktop: `hagema server` — dashboard monitoring + controller.

Di desktop (Mac/Windows) hagema jalan sebagai SERVER yang melayani kontrol
dari HP lewat web controller (`/api/chat`) dan/atau Telegram bot. Terminal
menampilkan DASHBOARD monitoring live (rich) yang memantau:

  - status web controller (URL, request count, token on/off)
  - status Telegram bot (token, chat_id yang diizinkan, pesan terproses)
  - provider aktif & failover order
  - sesi & memori
  - penggunaan token & biaya per provider

Semua layanan jalan di thread daemon; Ctrl+C menghentikan server dengan rapi.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .config import Config
from .remote import build_bridge
from .telegram import _poll_loop
from .web import Handler, ThreadingHTTPServer, _lan_ip


def _uptime(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}j {m}m {s}d"
    if m:
        return f"{m}m {s}d"
    return f"{s}d"


def _table(title: str, rows: list, header: Optional[list] = None) -> Panel:
    t = Table(title=title, title_justify="left", border_style="dim", expand=True,
              show_header=bool(header), header_style="bold cyan")
    if header:
        for h in header:
            t.add_column(h)
    else:
        t.add_column("", no_wrap=True)
    for row in rows:
        t.add_row(*row)
    return Panel(t, border_style="blue", padding=(0, 1))


def build_dashboard(bridge, stats: dict, start: float, web_url: str,
                    web_on: bool, tg_on: bool, tg_allow: list) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split_column(
        Layout(name="web", ratio=1),
        Layout(name="tg", ratio=1),
    )
    layout["right"].split_column(
        Layout(name="prov", ratio=1),
        Layout(name="usage", ratio=1),
    )

    # ---------- header ----------
    layout["header"].update(
        Panel(
            Text.assemble(
                ("🐝 hagema-agent ", "bold cyan"), (f"v{__version__} ", "bold"),
                ("· SERVER mode ", "bold yellow"),
                (f"· uptime {_uptime(time.time() - start)} ", "dim"),
                (f"· provider: {bridge.pm.current_name} → {bridge.pm.current.cfg.model}", "green"),
            ),
            border_style="cyan",
        )
    )

    # ---------- web ----------
    if web_on:
        web_rows = [
            ("URL", web_url),
            ("Requests", str(Handler.request_count)),
            ("Terakhir", Handler.last_message or "—"),
            ("Token", "AKTIF 🔒" if Handler.token else "nonaktif"),
            ("Status", "🟢 online"),
        ]
    else:
        web_rows = [("Status", "⚪ nonaktif (aktifkan di config / --web)")]
    layout["web"].update(_table("WEB CONTROLLER", web_rows))

    # ---------- telegram ----------
    if tg_on:
        tg_rows = [
            ("Status", "🟢 bot aktif"),
            ("Pesan diproses", str(stats.get("tg_messages", 0))),
            ("Terakhir", stats.get("tg_last", "—")),
            ("Izin chat_id", ", ".join(str(x) for x in tg_allow) or "(belum ada — bot menolak semua)"),
        ]
    else:
        tg_rows = [("Status", "⚪ nonaktif (aktifkan di config / --telegram)")]
    layout["tg"].update(_table("TELEGRAM BOT", tg_rows))

    # ---------- provider ----------
    prov_rows = []
    for name, p in bridge.pm._providers.items():
        marker = "★" if name == bridge.pm.current_name else " "
        key_ok = p.cfg.has_key(os.environ)
        key_state = "key OK" if key_ok else "key KOSONG"
        prov_rows.append((f"{marker} {name}", p.cfg.model, key_state))
    layout["prov"].update(_table("PROVIDER", prov_rows, header=["", "Model", "Key"]))

    # ---------- usage ----------
    usage_rows = []
    usage = bridge.agent.usage
    total = 0.0
    if usage:
        for name, s in usage.items():
            total += s["cost"]
            usage_rows.append((name, f"{s['input_tokens']:,.0f} in", f"{s['output_tokens']:,.0f} out",
                               f"${s['cost']:.4f}"))
        usage_rows.append(("TOTAL", "", "", f"${total:.4f}"))
    else:
        usage_rows.append(("(belum ada penggunaan)", "", "", ""))
    layout["usage"].update(_table("TOKEN & BIAYA", usage_rows, header=["Provider", "In", "Out", "Biaya"]))

    # ---------- footer ----------
    layout["footer"].update(
        Panel(
            "Kontrol dari HP: buka web controller di browser, atau kirim pesan ke bot Telegram. "
            "Ctrl+C untuk menghentikan server.",
            border_style="green",
        )
    )
    return layout


def run_server(args, console: Console) -> int:
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        console.print(f"[red]Config tidak ditemukan: {cfg_path}[/red]")
        console.print("Jalankan dulu: [bold]hagema setup[/bold]")
        return 1

    cfg = Config.load(cfg_path)
    cfg.load_env(Path(args.env))

    bridge = build_bridge(
        cfg_path, Path(args.env), session_name=args.session or "server",
        cwd=args.cwd, yes=args.yes,
    )

    # ---------- web controller ----------
    web_on = args.web
    web_url = ""
    web_server: Optional[ThreadingHTTPServer] = None
    if web_on:
        host = args.host or cfg.web_host
        port = args.port or cfg.web_port
        token = args.token or cfg.web_token or None
        try:
            web_server = ThreadingHTTPServer((host, port), Handler)
        except OSError as e:
            console.print(f"[red]Tidak bisa bind web {host}:{port} — {e}[/red]")
            return 1
        Handler.bridge = bridge
        Handler.token = token
        Handler.request_count = 0
        Handler.last_message = ""
        if host in ("127.0.0.1", "localhost"):
            web_url = f"http://127.0.0.1:{port}"
        elif host == "0.0.0.0":
            web_url = f"http://{_lan_ip()}:{port}"
        else:
            web_url = f"http://{host}:{port}"
        threading.Thread(target=web_server.serve_forever, daemon=True).start()

    # ---------- telegram bot ----------
    tg_on = args.telegram
    tg_token = args.tg_token or cfg.tg_token or os.environ.get("HAGEMA_TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_allow = list(cfg.tg_allow)
    if args.allow:
        for part in args.allow.split(","):
            part = part.strip()
            if part.isdigit():
                tg_allow.append(int(part))
    if tg_on and not tg_token:
        console.print("[yellow]Telegram diaktifkan tapi token kosong — bot dilewati.[/yellow]")
        tg_on = False

    stats = {"tg_messages": 0, "tg_last": ""}

    if web_on:
        console.print(f"[green]✓ Web controller: {web_url}[/green]")
    if tg_on:
        console.print(f"[green]✓ Telegram bot: aktif (izinkan: {tg_allow or 'belum ada'})[/green]")
    if not web_on and not tg_on:
        console.print("[yellow]Tidak ada controller aktif — dashboard saja. Gunakan --web / --telegram.[/yellow]")
    console.print("[bold cyan]hagema server[/bold cyan] — dashboard live. Ctrl+C untuk berhenti.\n")

    if tg_on:
        threading.Thread(
            target=_poll_loop,
            args=(tg_token, tg_allow, bridge, console),
            kwargs={"stats": stats, "quiet": True, "chat_lock": Handler.lock},
            daemon=True,
        ).start()

    start = time.time()
    try:
        with Live(build_dashboard(bridge, stats, start, web_url, web_on, tg_on, tg_allow),
                  console=console, refresh_per_second=2, screen=True) as live:
            while True:
                time.sleep(0.5)
                live.update(build_dashboard(bridge, stats, start, web_url, web_on, tg_on, tg_allow))
    except KeyboardInterrupt:
        console.print("\nServer dihentikan.")
    finally:
        if web_server is not None:
            web_server.shutdown()
    return 0
