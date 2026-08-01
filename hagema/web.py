"""Web app lokal untuk kontrol hagema dari HP (`hagema serve`).

Server HTTP ringan berbasis stdlib (tidak ada dependency tambahan) yang
memaparkan: UI chat di `GET /` dan API JSON di `POST /api/chat`.

Keamanan: opsional `--token` untuk membatasi akses. Akses dari luar Mac
disarankan lewat Tailscale (jaringan pribadi) atau ngrok (tunnel publik).
"""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from rich.console import Console

from .remote import build_bridge

PAGE = """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hagema-agent 🤖</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --border:#30363d; --text:#e6edf3;
          --muted:#8b949e; --accent:#2f81f7; --accent2:#a371f7; --user:#1f6feb; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,
         'Segoe UI', Roboto, sans-serif; height:100vh; display:flex;
         flex-direction:column; }
  header { padding:14px 20px; border-bottom:1px solid var(--border);
           background:linear-gradient(90deg, rgba(47,129,247,.12), rgba(163,113,247,.12));
           display:flex; align-items:center; gap:12px; }
  header h1 { font-size:17px; font-weight:600; }
  header .badge { font-size:11px; color:var(--muted); }
  #status { margin-left:auto; font-size:12px; color:var(--muted); }
  #status.on { color:#3fb950; }
  main { flex:1; overflow-y:auto; padding:20px; display:flex;
         flex-direction:column; gap:10px; }
  .msg { max-width:78%; padding:10px 14px; border-radius:14px;
         font-size:14px; line-height:1.55; white-space:pre-wrap;
         word-break:break-word; animation:pop .18s ease; }
  @keyframes pop { from { opacity:0; transform:translateY(4px); } to { opacity:1; } }
  .user { align-self:flex-end; background:var(--user); border-bottom-right-radius:4px; }
  .bot  { align-self:flex-start; background:var(--panel); border:1px solid var(--border);
          border-bottom-left-radius:4px; }
  .bot.err { border-color:#f85149; color:#ffa198; }
  .typing { align-self:flex-start; color:var(--muted); font-size:13px; padding:6px 2px; }
  footer { padding:14px 20px; border-top:1px solid var(--border); display:flex; gap:10px; }
  input { flex:1; background:var(--panel); color:var(--text); border:1px solid var(--border);
          border-radius:10px; padding:12px 14px; font-size:14px; outline:none; }
  #reset { background:var(--panel); color:var(--muted); border:1px solid var(--border);
           padding:12px 14px; }
  #reset:hover { color:#f85149; border-color:#f85149; filter:none; }
  input:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(47,129,247,.2); }
  button { background:linear-gradient(135deg, var(--accent), var(--accent2)); color:#fff;
           border:none; border-radius:10px; padding:12px 20px; font-size:14px;
           font-weight:600; cursor:pointer; transition:filter .15s; }
  button:hover { filter:brightness(1.12); }
  button:disabled { filter:grayscale(.5); cursor:not-allowed; }
  .hint { text-align:center; color:var(--muted); font-size:12px; margin-top:auto;
          padding-top:14px; }
</style>
</head>
<body>
<header>
  <h1>🐝 hagema-agent</h1>
  <span class="badge">agen AI otonom · failover otomatis</span>
  <span id="status">terhubung…</span>
</header>
<main id="chat"></main>
<footer>
  <input id="inp" placeholder="Tulis pesan… (Enter untuk kirim)" autocomplete="off">
  <button id="reset" title="Bersihkan sesi">Reset</button>
  <button id="send">Kirim</button>
</footer>
<script>
const chat = document.getElementById('chat');
const inp = document.getElementById('inp');
const send = document.getElementById('send');
const statusEl = document.getElementById('status');
let token = localStorage.getItem('hagema_token') || '';
const authHeaders = () => token ? { 'Authorization': 'Bearer ' + token } : {};

function addMsg(text, cls) {
  const el = document.createElement('div');
  el.className = 'msg ' + cls;
  el.textContent = text;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  return el;
}

async function api(path, body) {
  let res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  });
  if (res.status === 401) {
    const t = prompt('Masukkan token akses:');
    if (!t) throw new Error('dibatalkan (token diperlukan)');
    token = t;
    localStorage.setItem('hagema_token', token);
    res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    });
  }
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

async function sendMessage() {
  const text = inp.value.trim();
  if (!text) return;
  inp.value = '';
  addMsg(text, 'user');
  const ty = document.createElement('div');
  ty.className = 'typing'; ty.textContent = '🤖 memikirkan…'; chat.appendChild(ty);
  send.disabled = true;
  try {
    const data = await api('/api/chat', { message: text });
    ty.remove();
    addMsg(data.reply, data.reply.startsWith('ERROR') ? 'bot err' : 'bot');
  } catch (e) {
    ty.remove();
    addMsg('Gagal terhubung: ' + e.message, 'bot err');
  } finally {
    send.disabled = false;
    inp.focus();
  }
}

send.onclick = sendMessage;
inp.onkeydown = (e) => { if (e.key === 'Enter') sendMessage(); };

document.getElementById('reset').onclick = async () => {
  try {
    const data = await api('/api/reset', {});
    chat.innerHTML = '';
    addMsg('🧹 ' + data.reply, 'bot');
  } catch (e) {
    addMsg('Gagal reset: ' + e.message, 'bot err');
  }
};

async function refreshStatus() {
  try {
    const res = await fetch('/api/status', { headers: authHeaders() });
    if (res.ok) {
      const d = await res.json();
      statusEl.textContent = '● ' + d.provider;
      statusEl.className = 'on';
    } else if (res.status === 401) {
      statusEl.textContent = 'perlu token 🔒';
      statusEl.className = '';
    }
  } catch (_) { /* abaikan */ }
}
refreshStatus();
inp.focus();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    bridge = None
    token: Optional[str] = None
    # ThreadingHTTPServer menangani tiap request di thread terpisah, sedangkan
    # bridge (sesi + agent) adalah satu instance bersama — kunci ini memastikan
    # hanya satu chat yang diproses pada satu waktu agar file JSONL & state aman.
    lock = threading.Lock()

    def log_message(self, fmt, *args):  # diamkan log default
        pass

    # ---------- auth ----------

    def _unauthorized(self):
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"unauthorized"}')

    def _authorized(self) -> bool:
        if not self.token:
            return True
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.token}"
        return secrets.compare_digest(auth, expected)

    # ---------- routes ----------

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/status":
            if not self._authorized():
                return self._unauthorized()
            self._json(200, {"provider": self.bridge.status().splitlines()[0]})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/reset":
            if not self._authorized():
                return self._unauthorized()
            with self.lock:
                reply = self.bridge.reset()
            return self._json(200, {"reply": reply})
        if parsed.path != "/api/chat":
            return self._json(404, {"error": "not found"})
        if not self._authorized():
            return self._unauthorized()
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw)
            message = str(payload.get("message", "")).strip()
        except Exception:  # noqa: BLE001 - body tidak valid
            return self._json(400, {"error": "body JSON tidak valid"})
        if not message:
            return self._json(400, {"error": "message kosong"})
        with self.lock:
            reply = self.bridge.chat(message)
        return self._json(200, {"reply": reply})

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_web(args, console: Console) -> int:
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        console.print(f"[red]Config tidak ditemukan: {cfg_path}[/red]")
        console.print("Jalankan dulu: [bold]hagema setup[/bold]")
        return 1

    bridge = build_bridge(
        cfg_path, Path(args.env), session_name=args.session or "web",
        cwd=args.cwd, yes=args.yes,
    )
    Handler.bridge = bridge
    Handler.token = args.token or None

    host = args.host
    port = args.port
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        console.print(f"[red]Tidak bisa bind {host}:{port} — {e}[/red]")
        console.print("Coba port lain: [bold]hagema serve --port 9000[/bold]")
        return 1
    url = f"http://127.0.0.1:{port}" if host in ("127.0.0.1", "localhost") else f"http://{host}:{port}"

    console.print(f"[bold cyan]hagema web[/bold cyan] — {url}")
    console.print(f"Provider: [bold]{bridge.pm.current_name}[/bold] → {bridge.pm.current.cfg.model}")
    if Handler.token:
        console.print("[yellow]Token akses diaktifkan — simpan token ini![/yellow]")
    else:
        console.print("[yellow]⚠ Tanpa --token, siapa pun yang bisa mengakses port ini bisa memakai agenmu.[/yellow]")
    if host == "0.0.0.0":
        console.print("[dim]Terbuka ke jaringan lokal. Gunakan Tailscale/ngrok untuk akses dari luar.[/dim]")
    console.print("Ctrl+C untuk berhenti.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\nWeb server berhenti.")
    return 0
