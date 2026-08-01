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
