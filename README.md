# hagema-agent 🤖

Agen AI otonom ala **Hermes Agent** (Nous Research) yang kamu bangun sendiri — di Python, tanpa framework berat.

**Fitur unggulan (sesuai kebutuhan kamu):**
> Pakai DeepSeek → token/kuota habis → agen **otomatis merekap sesi** → **pindah ke provider lain** (OpenRouter/OpenAI/Ollama) → lanjut kerja tanpa kehilangan konteks.

## Fitur

- 🔁 **Cross-provider failover** — rekap otomatis saat kena `quota`, `rate_limit`, atau konteks penuh
- 📝 **Recap sesi** — ringkasan terstruktur (tujuan, progress, keputusan, file, langkah lanjut) yang disuntikkan ke provider berikutnya
- 🧰 **Tool calling** — jalankan terminal, baca/tulis file, list direktori, muat skill
- 🧠 **Memori jangka panjang** (`MEMORY.md`) — selalu dimuat di setiap sesi
- 🛠️ **Sistem skill** (standar `agentskills.io`) — prosedur yang bisa dipanggil lewat `/skill`
- 💰 **Pelacakan biaya** — estimasi token & USD per provider
- 📡 **Kontrol CLI agent lain** (`hagema agents`) — deteksi otomatis agent yang terpasang (opencode, claude, codex, aichat, gemini, aider, cursor) + install yang belum ada
- 📚 **Riwayat lengkap untuk bahan belajar AI** — semua percakapan (CLI/web/Telegram) direkam detail (token, biaya, tool calls, timestamp) ke `~/.hagema/history/`
- 🧠 **Auto-memory ala Hermes** — rekap sesi otomatis masuk `MEMORY.md` saat keluar (bisa dimatikan via `auto_memory: false` di config)
- 🧪 **Mode mock** — demo lengkap tanpa API key (termasuk demo failover)

## Quickstart (install semudah mungkin)

### Opsi 1 — Installer satu perintah (paling mudah) 🚀

```bash
cd hagema-agent
./install.sh
```

Installer otomatis: bikin venv → install package → langsung tanya mau jalankan wizard setup (pilih provider + isi API key).

Lalu pakai:

```bash
source .venv/bin/activate   # aktifkan venv (sekali per terminal)
hagema                       # mulai ngobrol
```

### Opsi 2 — Makefile

```bash
make install   # = ./install.sh
make setup     # wizard konfigurasi
make run       # mulai chat
```

### Opsi 3 — Install manual / global (ala opencode)

```bash
pip install -e .          # development
# atau
pip install .             # normal
# atau global
pipx install .

hagema setup              # wizard interaktif: pilih provider, model, isi API key
hagema                    # mulai chat
```

Wizard setup menulis `~/.hagema/config.yaml` + `~/.hagema/.env` otomatis. **Kamu tidak perlu mengedit file apapun secara manual.**

> 📄 `config.example.yaml` di repo ini adalah **template referensi** untuk edit manual — salin ke `~/.hagema/config.yaml` (atau cukup jalankan `hagema setup`, yang otomatis menuliskannya).

> **Kenapa API key di `.env`, bukan di config?**
> Model, base_url, dan provider diatur di `config.yaml` — itu bukan rahasia dan boleh di-commit. Yang disimpan di `.env` HANYA API key, karena key adalah secret yang tidak boleh ikut ke git. Ini praktik keamanan standar (12-factor). Dengan `hagema setup`, kamu nggak perlu sentuh `.env` manual — wizard yang nulis.

### Perintah lengkap

```bash
hagema                         # chat REPL (provider default)
hagema --mock quota            # demo failover tanpa API key
hagema model                   # lihat provider terkonfigurasi
hagema model ollama            # ganti provider default jadi ollama
hagema model ollama qwen3:14b  # ganti provider + model sekaligus
hagema models                  # deteksi daftar model dari API provider aktif
hagema models openrouter       # deteksi model dari provider tertentu
hagema doctor                  # cek instalasi & config
hagema agents                  # deteksi CLI agent yang terpasang di mesin
hagema agents install aider    # install CLI agent yang belum ada (mis. aider)
hagema history                 # statistik riwayat percakapan (bahan belajar AI)
hagema --session kerja-ssmi    # sesi bernama (riwayat tersimpan)
hagema --yes                   # auto-approve perintah terminal
```

### Di dalam REPL

```
kamu> buatkan file .sql untuk tabel baru
kamu> /models                     # deteksi model yang tersedia di provider aktif
kamu> /switch openrouter          # rekap sesi lalu pindah provider
kamu> /recap                      # rekap sekarang tanpa pindah
kamu> /usage                      # token & biaya
kamu> /remember pakai ENUM untuk status box
```

## Konsep failover

Urutan ada di `config.yaml` → `failover_order`. Contoh: `[deepseek, openrouter, openai, ollama]`.

1. Provider aktif kena error quota/rate-limit/konteks.
2. Agen merekap sesi memakai **provider tujuan** (yang masih hidup).
3. Recap disuntikkan ke system prompt + riwayat dipangkas.
4. Percakapan lanjut dengan provider baru, otomatis.

## Menambah provider

Tambahkan blok di `config.yaml` (harus OpenAI-compatible):

```yaml
providers:
  deepseek:
    base_url: https://api.deepseek.com
    model: deepseek-chat
    api_key_env: DEEPSEEK_API_KEY
    context_limit: 65536
    price_per_1m_input: 0.27
    price_per_1m_output: 1.10
```

## Membuat skill

```bash
mkdir -p ~/.hagema/skills/ssmi-sql
nano ~/.hagema/skills/ssmi-sql/SKILL.md
```

```markdown
---
name: ssmi-sql
description: Membantu menulis query SQL MySQL untuk project SSMI. Gunakan saat user minta bantuan query atau struktur tabel.
---

# Prosedur
1. Minta konteks tabel terkait (biasanya ada di file .sql project).
2. Tulis query dengan alias yang jelas.
3. Jangan lupa WHERE untuk UPDATE/DELETE.
```

Lalu di REPL: `/skills` dan `/skill ssmi-sql`.

## Struktur project

```
hagema-agent/
├── main.py                 # entry point
├── config.example.yaml     # template provider & failover order
├── hagema/
│   ├── config.py           # loader config + .env
│   ├── providers.py        # OpenAI-compatible provider + error classifier + mock
│   ├── session.py          # JSONL + logic rekap
│   ├── agent.py            # loop + failover otomatis
│   ├── tools.py            # terminal, file, skill tools
│   ├── memory.py           # MEMORY.md
│   ├── skills.py           # registry skill
│   └── cli.py              # REPL
└── tests/test_agent.py     # unit test
```

## Kontrol CLI agent lain: `hagema agents` 📡

Scan PATH dan laporkan CLI agent coding yang terpasang (opencode, claude, codex, aichat, gemini, aider, cursor, hermes) beserta versinya, dan tawarkan install untuk yang belum ada:

```bash
hagema agents                  # lihat semua: TERPASANG / belum ada + perintah install
hagema agents install aider    # install aider (pipx install aider-chat) setelah konfirmasi
hagema agents --yes install opencode  # install tanpa konfirmasi
```

Bisa juga dikontrol dari HP: di Telegram ketik `/agents` untuk melihat status CLI agent di laptop/PC, atau buka endpoint web `GET /api/agents` (token opsional).

## Riwayat lengkap untuk bahan belajar AI 📚

Semua percakapan (CLI, web, Telegram, server) direkam **sedetail mungkin** ke `~/.hagema/history/<YYYY-MM-DD>/<sesi>.jsonl`:

- teks user & balasan asisten
- tool calls (nama, argumen, output)
- provider aktif, model, token in/out, estimasi biaya
- timestamp, durasi, sumber (cli/web/telegram)

```bash
hagema history   # ringkasan: total giliran, sesi, token, biaya, sumber
```

Format JSONL (satu objek per baris) siap dipakai untuk analisis, eval, atau bahan fine-tune nanti.

## Auto-memory ala Hermes 🧠

Saat kamu keluar dari REPL (Ctrl+C atau `/exit`), sesi di-rekap dan hasilnya **otomatis ditambahkan ke `MEMORY.md`** dengan judul `## Auto-recap <tanggal>` — jadi pengetahuan dari sesi kemarin selalu tersedia di sesi berikutnya. Matikan kalau tidak mau:

```yaml
# ~/.hagema/config.yaml
auto_memory: false
```

## Mode Server Desktop (dashboard monitoring) 🖥️

Di desktop (Mac/Windows), hagema bisa jalan sebagai **server** — terminal menampilkan **dashboard monitoring live**, sementara web controller dan/atau Telegram bot melayani kontrol dari HP:

```bash
hagema server                 # dashboard + web + telegram (sesuai config)
hagema server --web           # paksa aktifkan web controller
hagema server --telegram --tg-token TOKEN --allow 123,456
hagema server --no-telegram   # matikan bot, web saja
```

Dashboard menampilkan: status web controller (URL, jumlah request, token), status Telegram (pesan terproses, chat_id diizinkan), provider aktif & status key, penggunaan token & biaya — di-refresh live tiap ~0.5 detik. Ctrl+C menghentikan server dengan rapi.

> Setting web/Telegram bisa ditulis sekali di config lewat `hagema setup` (bagian "Akses dari HP"), lalu `hagema server` cukup dipanggil tanpa flag.

## Kontrol dari HP 📱

Tiga cara memakai hagema dari handphone:

### Opsi 1 — Telegram bot (paling praktis) 🐦

```bash
# 1. Buat bot di Telegram: chat @BotFather → /newbot → simpan token
# 2. Jalankan bot (token juga bisa via env HAGEMA_TELEGRAM_TOKEN)
hagema telegram --token 123456:ABC-DEF...

# 3. Di Telegram, kirim pesan apa pun ke bot-mu.
#    Bot akan menolak dan memberi tahu chat_id-mu → restart dengan izin:
hagema telegram --token 123456:ABC-DEF... --allow <chat_id>
```

Perintah di bot: `/status`, `/usage`, `/providers`, `/switch <nama>`, `/reset`, `/help` — atau kirim pesan bebas untuk ngobrol.

### Opsi 2 — Web app di browser 🌐

```bash
hagema serve                         # buka http://127.0.0.1:8765 di browser
hagema serve --host 0.0.0.0 --token rahasiaku   # akses dari HP se-LAN + token
hagema server --web --token rahasiaku           # server + dashboard + web controller
```

Untuk akses dari luar rumah dengan aman: pasang [Tailscale](https://tailscale.com) di Mac & HP, lalu buka `http://<ip-tailscale>:8765` di browser HP.

### Opsi 3 — SSH dari HP 🔐

Install Tailscale + aplikasi SSH (Termius/Blink) di HP, lalu SSH ke Mac:

```bash
ssh user@<ip-tailscale>   # dari Termius/Blink di HP
hagema                    # CLI penuh, sama seperti di Mac
```

> Semua mode jarak jauh menolak perintah terminal kecuali diberi `--yes` — pastikan hanya kamu yang punya aksesnya.
>
> `hagema serve` dan `hagema telegram` berjalan di foreground — biarkan terminal tetap terbuka, atau jalankan persistent pakai `tmux` / `nohup`:
>
> ```bash
> nohup hagema serve --host 0.0.0.0 --token rahasiaku > ~/hagema-web.log 2>&1 &
> tmux new -d -s hagema 'hagema telegram --token TOKEN --allow CHAT_ID'
> ```

## Test

```bash
python -m unittest discover tests -v
```

## Publikasi ke PyPI (biar orang lain bisa `pip install hagema-agent`)

```bash
pip install build
python -m build
pip install twine
python -m twine upload dist/*
```
