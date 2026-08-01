"""Telegram bot untuk kontrol hagema dari HP (`hagema telegram`).

Bot long-polling memakai Bot API standar (stdlib urllib — tanpa dependency
tambahan). Token dari `--token`, env `HAGEMA_TELEGRAM_TOKEN`, atau
`TELEGRAM_BOT_TOKEN`.

Keamanan: default HANYA melayani chat_id yang terdaftar di `--allow`
(atau config). Jalankan sekali, bot akan memberi tahu chat_id-mu saat
pertama pesan; lalu restart dengan `--allow <chat_id>` untuk mengizinkan.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

from rich.console import Console

from .remote import build_bridge

API = "https://api.telegram.org/bot{token}/{method}"

HELP = (
    "Perintah yang tersedia:\n"
    "/start — mulai & info\n"
    "/help — bantuan ini\n"
    "/status — provider & sesi aktif\n"
    "/usage — token & biaya\n"
    "/providers — daftar provider\n"
    "/agents — CLI agent yang terpasang di mesin\n"
    "/agent <nama> <prompt> — jalankan CLI agent lain (mis. opencode)\n"
    "/switch <nama> — pindah provider\n"
    "/reset — bersihkan sesi\n"
    "\nSelain itu, kirim pesan bebas untuk ngobrol dengan agen."
)


def _call(token: str, method: str, **params) -> dict:
    url = API.format(token=token, method=method)
    data = json.dumps(params).encode("utf-8") if params else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _send(token: str, chat_id: int, text: str) -> None:
    # batasi panjang pesan Telegram (4096)
    for i in range(0, len(text), 4000):
        _call(token, "sendMessage", chat_id=chat_id, text=text[i:i + 4000])


def _command(text: str) -> str:
    parts = (text or "").strip().split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    return cmd.split("@")[0]  # abaikan sufiks @username (perilaku Telegram di grup)


def run_telegram(args, console: Console) -> int:
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        console.print(f"[red]Config tidak ditemukan: {cfg_path}[/red]")
        console.print("Jalankan dulu: [bold]hagema setup[/bold]")
        return 1

    token = args.token or os.environ.get("HAGEMA_TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        console.print(
            "[red]Token Telegram tidak ditemukan.[/red]\n"
            "Buat bot di @BotFather (Telegram) lalu beri token: "
            "[bold]hagema telegram --token <TOKEN>[/bold]\n"
            "atau export HAGEMA_TELEGRAM_TOKEN=... "
        )
        return 1

    allow: List[int] = []
    if args.allow:
        for part in args.allow.split(","):
            part = part.strip()
            if part.isdigit():
                allow.append(int(part))

    bridge = build_bridge(
        cfg_path, Path(args.env), session_name=args.session or "telegram",
        cwd=args.cwd, yes=args.yes,
    )

    # Hormati cfg.tg_allow dari config (ditulis `hagema setup`) supaya konsisten
    # dengan mode server.
    for cid in bridge.cfg.tg_allow:
        if cid not in allow:
            allow.append(cid)

    console.print("[bold cyan]hagema telegram[/bold cyan] — bot aktif (Ctrl+C berhenti)")
    console.print(f"Provider: [bold]{bridge.pm.current_name}[/bold] → {bridge.pm.current.cfg.model}")
    if allow:
        console.print(f"Izin chat_id: {allow}")
    else:
        console.print(
            "[yellow]⚠ Mode aman: belum ada --allow. Bot akan menolak pesan "
            "dan memberi tahu chat_id-mu — restart dengan --allow <chat_id>.[/yellow]"
        )

    _poll_loop(token, allow, bridge, console)
    return 0


def _poll_loop(token: str, allow: List[int], bridge, console: Console,
               stats: Optional[dict] = None, quiet: bool = False,
               chat_lock: Optional[object] = None) -> None:
    """Loop long-polling; dipakai CLI dan mode server (thread).

    `stats` (opsional) adalah dict bersama yang di-update untuk dashboard.
    `quiet=True` menekan log agar tidak merusak tampilan dashboard.
    `chat_lock` (opsional) dipakai mode server supaya chat web & Telegram
    tidak saling berebut state sesi yang sama.
    """
    offset = 0
    try:
        while True:
            try:
                data = _call(token, "getUpdates", offset=offset, timeout=30)
            except Exception as e:  # noqa: BLE001 - jaringan fluktuatif, coba lagi
                if not quiet:
                    console.print(f"[dim]getUpdates gagal: {e} — coba lagi...[/dim]")
                time.sleep(3)
                continue

            if not data.get("ok"):
                # Token tidak valid / API error — jangan tight-loop, jeda sebelum coba lagi
                desc = data.get("description") or "respons tidak valid"
                if not quiet:
                    console.print(f"[red]Telegram API: {desc}[/red]")
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or {}
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text") or ""
                if not chat_id:
                    continue

                try:
                    if chat_id not in allow:
                        _send(
                            token, chat_id,
                            f"⛔ Kamu belum diizinkan. chat_id-mu: {chat_id}\n"
                            f"Restart bot dengan: hagema telegram --allow {chat_id}",
                        )
                        continue

                    # /agent menjalankan CLI agent lain (opencode/hermes/dll) yang
                    # TIDAK menyentuh sesi bersama — jalankan TANPA chat_lock supaya
                    # run yang lama (hingga timeout) tidak memblokir web controller.
                    if _command(text) == "/agent":
                        parts = (text or "").split(maxsplit=1)
                        rest = parts[1].strip() if len(parts) > 1 else ""
                        name, _, prompt = rest.partition(" ")
                        _send(token, chat_id, bridge.run_cli_agent(name, prompt))
                        continue

                    # Mode server: semua operasi bridge (chat, switch, reset) dipakai
                    # bersama web controller — kunci sama supaya state sesi aman.
                    def _process():
                        cmd = _command(text)
                        if cmd == "/start":
                            _send(token, chat_id, "🐝 Selamat datang di hagema-agent!\n" + HELP)
                        elif cmd == "/help":
                            _send(token, chat_id, HELP)
                        elif cmd == "/status":
                            _send(token, chat_id, bridge.status())
                        elif cmd == "/usage":
                            _send(token, chat_id, bridge.usage())
                        elif cmd == "/providers":
                            _send(token, chat_id, bridge.providers_text())
                        elif cmd == "/agents":
                            from .agents import agents_text
                            _send(token, chat_id, agents_text())
                        elif cmd == "/switch":
                            target = (text or "").split(maxsplit=1)[1] if len((text or "").split()) > 1 else ""
                            _send(token, chat_id, bridge.switch(target) if target else "Pakai: /switch <nama>")
                        elif cmd == "/reset":
                            _send(token, chat_id, bridge.reset())
                        else:
                            if not quiet:
                                console.print(f"[dim]chat {chat_id}: {text[:60]}[/dim]")
                            reply = bridge.chat(text)
                            _send(token, chat_id, reply)
                            if stats is not None:
                                stats["tg_messages"] = stats.get("tg_messages", 0) + 1
                                stats["tg_last"] = text[:60]

                    if chat_lock is not None:
                        with chat_lock:
                            _process()
                    else:
                        _process()
                except Exception as e:  # noqa: BLE001 - satu pesan gagal jangan matikan bot
                    if not quiet:
                        console.print(f"[red]Gagal proses pesan dari {chat_id}: {e}[/red]")
                    continue
    except KeyboardInterrupt:
        if not quiet:
            console.print("\nTelegram bot berhenti.")
