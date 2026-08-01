"""Unit test inti hagema-agent: loop, tool calling, failover + rekap."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hagema.agent import Agent  # noqa: E402
from hagema.config import Config  # noqa: E402
from hagema.history import HistoryRecorder  # noqa: E402
from hagema.memory import Memory  # noqa: E402
from hagema.providers import MockProvider, ProviderConfig, ProviderManager  # noqa: E402
from hagema.session import Session  # noqa: E402
from hagema.skills import SkillsRegistry  # noqa: E402
from hagema.tools import ToolExecutor  # noqa: E402


def make_manager(behaviors: dict, default: str, failover: list) -> ProviderManager:
    cfg = Config({"default_provider": default, "failover_order": failover, "providers": {}})
    pm = ProviderManager.__new__(ProviderManager)
    pm.config = cfg
    pm.env = {}
    pm._providers = {}
    for name, behavior in behaviors.items():
        pcfg = ProviderConfig(name=name, base_url="http://127.0.0.1:1/v1", model="mock-model")
        pm._providers[name] = MockProvider(pcfg, behavior=behavior)
    pm.current_name = default
    return pm


class AgentCoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.session = Session(self.tmp / "test.jsonl")
        self.skills = SkillsRegistry(self.tmp / "skills")
        self.memory = Memory(self.tmp / "MEMORY.md")
        self.executor = ToolExecutor(cwd=self.tmp)

    def _agent(self, pm) -> Agent:
        return Agent(None, pm, self.session, self.executor, self.skills, self.memory)

    def test_basic_reply(self):
        pm = make_manager({"m": "normal"}, "m", ["m"])
        reply = self._agent(pm).run("halo")
        self.assertIn("Mock[m]", reply)
        roles = [m.get("role") for m in self.session.messages]
        self.assertEqual(roles[0], "user")

    def test_tool_call_execution(self):
        pm = make_manager({"m": "tool_call"}, "m", ["m"])
        reply = self._agent(pm).run("lihat folder")
        self.assertIn("Mock[m]", reply)
        roles = [m.get("role") for m in self.session.messages]
        self.assertIn("tool", roles)

    def test_failover_on_quota(self):
        pm = make_manager({"a": "quota", "b": "normal"}, "a", ["a", "b"])
        reply = self._agent(pm).run("tes failover")
        self.assertEqual(pm.current_name, "b")
        self.assertIn("Mock[b]", reply)

    def test_failover_on_context(self):
        pm = make_manager({"a": "context", "b": "normal"}, "a", ["a", "b"])
        reply = self._agent(pm).run("tes context")
        self.assertEqual(pm.current_name, "b")

    def test_failover_all_exhausted(self):
        pm = make_manager({"a": "quota", "b": "quota"}, "a", ["a", "b"])
        reply = self._agent(pm).run("tes habis semua")
        self.assertIn("ERROR", reply)
        # current_name tetap di provider terakhir yang dicoba
        self.assertEqual(pm.current_name, "b")

    def test_recap_generated_on_failover(self):
        pm = make_manager({"a": "quota", "b": "normal"}, "a", ["a", "b"])
        self._agent(pm).run("tolong kerjakan tugas X")
        self.assertIsNotNone(self.session.recap)
        self.assertTrue(self.session.recap.strip())

    def test_auth_error_no_failover(self):
        # error auth tidak boleh memicu failover (bukan karena token habis)
        pm = make_manager({"a": "auth", "b": "normal"}, "a", ["a", "b"])
        reply = self._agent(pm).run("tes")
        self.assertEqual(pm.current_name, "a")
        self.assertIn("ERROR", reply)

    def test_choices_none_raises_clear_error(self):
        """Respons 200 dengan choices:null harus jadi ProviderError jelas, bukan TypeError mentah."""
        from hagema.providers import Provider, ProviderConfig, ProviderError

        class FakeResp:
            choices = None

        class _FakeCompletions:
            def create(self, **kwargs):
                return FakeResp()

        class _FakeChat:
            def __init__(self):
                self.completions = _FakeCompletions()

        class FakeClient:
            def __init__(self):
                self.chat = _FakeChat()

        p = Provider.__new__(Provider)
        p.cfg = ProviderConfig(name="openrouter", base_url="x", model="m", api_key_env="")
        p._client = FakeClient()
        with self.assertRaises(ProviderError) as ctx:
            p.chat([{"role": "user", "content": "halo"}])
        self.assertIn("tanpa choices", str(ctx.exception))
        self.assertEqual(ctx.exception.provider, "openrouter")


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.session = Session(self.tmp / "s.jsonl")

    def test_persistence_roundtrip(self):
        self.session.add({"role": "user", "content": "hai"})
        self.session.add({"role": "assistant", "content": "halo"})
        loaded = Session(self.session.path)
        self.assertEqual(len(loaded.messages), 2)
        self.assertEqual(loaded.messages[1]["content"], "halo")

    def test_chat_messages_include_system(self):
        self.session.add({"role": "user", "content": "x"})
        msgs = self.session.chat_messages("SYS")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[0]["content"], "SYS")

    def test_compress_tail_after_recap(self):
        self.session.add({"role": "user", "content": "1"})
        self.session.add({"role": "assistant", "content": "2"})
        self.session.add({"role": "user", "content": "3"})
        self.session.compress_tail(1)
        self.assertEqual(len(self.session.messages), 1)

    def test_compress_tail_persisted(self):
        """Kompresi harus tulis ulang file JSONL, bukan hanya memori."""
        self.session.add({"role": "user", "content": "1"})
        self.session.add({"role": "user", "content": "2"})
        self.session.add({"role": "user", "content": "3"})
        self.session.compress_tail(1)
        loaded = Session(self.session.path)
        self.assertEqual(len(loaded.messages), 1)
        self.assertEqual(loaded.messages[0]["content"], "3")

    def test_recap_persisted(self):
        """Recap harus tetap ada setelah sesi dibuka ulang."""
        self.session.add({"role": "user", "content": "hai"})
        self.session.recap = "## Konteks\nsesi demo"
        self.session._persist_recap()
        loaded = Session(self.session.path)
        self.assertEqual(loaded.recap, "## Konteks\nsesi demo")


class SkillsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        skill_dir = self.tmp / "skills" / "contoh"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: contoh\ndescription: Skill contoh untuk test\n---\n# Contoh\nLangkah 1.\n",
            encoding="utf-8",
        )
        self.registry = SkillsRegistry(self.tmp / "skills")

    def test_discover(self):
        self.assertIn("contoh", [s.name for s in self.registry.list()])

    def test_load_markdown(self):
        skill = self.registry.get("contoh")
        self.assertIsNotNone(skill)
        self.assertIn("Langkah 1", skill.load_markdown())


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg_path = self.tmp / "config.yaml"
        self._saved_env = os.environ.get("DEEPSEEK_API_KEY")
        os.environ.pop("DEEPSEEK_API_KEY", None)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = self._saved_env

    def test_save_and_reload(self):
        cfg = Config({"default_provider": "deepseek", "providers": {}, "failover_order": []})
        cfg.save(self.cfg_path)
        reloaded = Config.load(self.cfg_path)
        self.assertEqual(reloaded.default_provider, "deepseek")

    def test_set_default_provider(self):
        raw = {
            "default_provider": "deepseek",
            "providers": {
                "deepseek": {"base_url": "x", "model": "m", "api_key_env": "K"},
                "ollama": {"base_url": "y", "model": "n", "api_key_env": ""},
            },
            "failover_order": ["deepseek", "ollama"],
        }
        cfg = Config(raw)
        cfg.set_default_provider("ollama")
        self.assertEqual(cfg.default_provider, "ollama")

    def test_set_model(self):
        raw = {
            "default_provider": "deepseek",
            "providers": {
                "deepseek": {"base_url": "x", "model": "deepseek-chat", "api_key_env": "K"},
            },
            "failover_order": ["deepseek"],
        }
        cfg = Config(raw)
        cfg.set_model("deepseek", "deepseek-reasoner")
        self.assertEqual(cfg.providers["deepseek"].model, "deepseek-reasoner")
        self.assertEqual(cfg.raw["providers"]["deepseek"]["model"], "deepseek-reasoner")
        with self.assertRaises(KeyError):
            cfg.set_model("tidak-ada", "x")

    def test_set_model_roundtrip(self):
        """set_model harus persist saat config disimpan & dimuat ulang."""
        raw = {
            "default_provider": "deepseek",
            "providers": {
                "deepseek": {"base_url": "x", "model": "deepseek-chat", "api_key_env": "K"},
            },
            "failover_order": ["deepseek"],
        }
        cfg = Config(raw)
        cfg.set_model("deepseek", "deepseek-reasoner")
        cfg.save(self.cfg_path)
        reloaded = Config.load(self.cfg_path)
        self.assertEqual(reloaded.providers["deepseek"].model, "deepseek-reasoner")

    def test_cli_model_two_positionals(self):
        """hagema model <provider> <model> harus parse dua argumen posisi."""
        from hagema.cli import main

        raw = {
            "default_provider": "ollama",
            "providers": {
                "ollama": {"base_url": "x", "model": "qwen3:32b", "api_key_env": ""},
            },
            "failover_order": ["ollama"],
        }
        Config(raw).save(self.cfg_path)

        # satu argumen: ganti provider default saja
        code = main(["model", "ollama", "--config", str(self.cfg_path), "--env", str(self.tmp / ".env")])
        self.assertEqual(code, 0)

        # dua argumen: ganti provider + model
        code = main(["model", "ollama", "qwen3:14b", "--config", str(self.cfg_path), "--env", str(self.tmp / ".env")])
        self.assertEqual(code, 0)
        reloaded = Config.load(self.cfg_path)
        self.assertEqual(reloaded.providers["ollama"].model, "qwen3:14b")
        self.assertEqual(reloaded.default_provider, "ollama")

    def test_mock_list_models(self):
        pm = make_manager({"m": "normal"}, "m", ["m"])
        models = pm.current.list_models()
        self.assertEqual(models, ["mock-model"])

    def test_env_loading(self):
        env_file = self.tmp / ".env"
        env_file.write_text("DEEPSEEK_API_KEY=sk-test\n", encoding="utf-8")
        cfg = Config({"default_provider": "deepseek", "providers": {}, "failover_order": []})
        cfg.load_env(env_file)
        self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), "sk-test")

    def test_remote_settings_parsing(self):
        raw = {
            "default_provider": "deepseek",
            "providers": {},
            "failover_order": [],
            "web": {"enabled": True, "host": "0.0.0.0", "port": 8765, "token": "abc"},
            "telegram": {"enabled": True, "token": "t", "allow": ["123", "456"]},
        }
        cfg = Config(raw)
        self.assertTrue(cfg.web_enabled)
        self.assertEqual(cfg.web_host, "0.0.0.0")
        self.assertEqual(cfg.web_port, 8765)
        self.assertEqual(cfg.web_token, "abc")
        self.assertTrue(cfg.tg_enabled)
        self.assertEqual(cfg.tg_token, "t")
        self.assertEqual(cfg.tg_allow, [123, 456])

    def test_remote_settings_defaults(self):
        cfg = Config({"default_provider": "deepseek", "providers": {}, "failover_order": []})
        self.assertFalse(cfg.web_enabled)
        self.assertEqual(cfg.web_port, 8765)
        self.assertFalse(cfg.tg_enabled)
        self.assertEqual(cfg.tg_allow, [])

    def test_history_and_auto_memory_defaults(self):
        cfg = Config({"default_provider": "deepseek", "providers": {}, "failover_order": []})
        self.assertTrue(cfg.auto_memory)
        self.assertEqual(cfg.history_dir.name, "history")

    def test_history_and_auto_memory_parsing(self):
        raw = {
            "default_provider": "deepseek",
            "providers": {},
            "failover_order": [],
            "history_dir": "~/myhistory",
            "auto_memory": False,
        }
        cfg = Config(raw)
        self.assertFalse(cfg.auto_memory)
        self.assertIn("myhistory", str(cfg.history_dir))


class HeadlessBridgeTest(unittest.TestCase):
    """Bridge headless (dipakai web & Telegram bot) harus aman tanpa console."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.session = Session(self.tmp / "b.jsonl")
        self.skills = SkillsRegistry(self.tmp / "skills")
        self.memory = Memory(self.tmp / "MEMORY.md")
        self.executor = ToolExecutor(cwd=self.tmp, confirm=lambda _c: False)
        self.pm = make_manager({"m": "normal"}, "m", ["m"])
        self.agent = Agent(None, self.pm, self.session, self.executor, self.skills, self.memory)
        from hagema.remote import HeadlessBridge
        self.bridge = HeadlessBridge(None, self.pm, self.session, self.skills, self.memory, self.agent)

    def test_chat(self):
        reply = self.bridge.chat("halo")
        self.assertIn("Mock[m]", reply)

    def test_chat_empty(self):
        self.assertEqual(self.bridge.chat("   "), "(pesan kosong)")

    def test_reset(self):
        self.bridge.chat("halo")
        self.bridge.reset()
        self.assertEqual(self.session.messages, [])

    def test_switch(self):
        result = self.bridge.switch("m")
        self.assertIn("→ m", result)
        result = self.bridge.switch("tidak-ada")
        self.assertIn("tidak ada", result)

    def test_run_cli_agent_denied_without_yes(self):
        reply = self.bridge.run_cli_agent("opencode", "halo")
        self.assertIn("DITOLAK", reply)

    def test_run_cli_agent_with_yes_unknown_agent(self):
        from hagema.remote import HeadlessBridge
        b = HeadlessBridge(None, self.pm, self.session, self.skills, self.memory,
                           self.agent, yes=True)
        reply = b.run_cli_agent("agent-tidak-ada", "halo")
        self.assertIn("tidak terpasang", reply)

    def test_usage_text(self):
        self.bridge.chat("halo")
        text = self.bridge.usage()
        self.assertIn("TOTAL", text)


class WebServerTest(unittest.TestCase):
    """Integration test web app: server nyata + endpoint /api/chat, /api/reset, /api/status."""

    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer

        from hagema.remote import HeadlessBridge
        from hagema.web import Handler

        self.tmp = Path(tempfile.mkdtemp())
        session = Session(self.tmp / "web.jsonl")
        skills = SkillsRegistry(self.tmp / "skills")
        memory = Memory(self.tmp / "MEMORY.md")
        executor = ToolExecutor(cwd=self.tmp, confirm=lambda _c: False)
        pm = make_manager({"m": "normal"}, "m", ["m"])
        agent = Agent(None, pm, session, executor, skills, memory)
        Handler.bridge = HeadlessBridge(None, pm, session, skills, memory, agent)
        Handler.token = None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def _request(self, path: str, payload: dict | None = None, token: str | None = None):
        import json as _json
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{self.port}{path}"
        data = _json.dumps(payload).encode() if payload is not None else None
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, _json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # urlopen melempar HTTPError untuk status 4xx/5xx — ubah jadi (status, body)
            try:
                body = _json.loads(e.read().decode() or b"{}")
            except Exception:  # noqa: BLE001 - body error boleh gagal dibaca (koneksi reset)
                body = {}
            return e.code, body

    def test_status(self):
        status, body = self._request("/api/status")
        self.assertEqual(status, 200)
        self.assertIn("m", body["provider"])

    def test_chat(self):
        status, body = self._request("/api/chat", {"message": "halo"})
        self.assertEqual(status, 200)
        self.assertIn("Mock[m]", body["reply"])

    def test_reset(self):
        self._request("/api/chat", {"message": "halo"})
        status, body = self._request("/api/reset", {})
        self.assertEqual(status, 200)
        self.assertIn("dibersihkan", body["reply"])

    def test_unauthorized(self):
        from hagema.web import Handler
        Handler.token = "rahasia"
        try:
            status, body = self._request("/api/chat", {"message": "x"})
            self.assertEqual(status, 401)
            status, body = self._request("/api/chat", {"message": "x"}, token="rahasia")
            self.assertEqual(status, 200)
        finally:
            Handler.token = None

    def test_stats_endpoint(self):
        from hagema.web import Handler
        Handler.request_count = 0
        Handler.last_message = ""
        self._request("/api/chat", {"message": "halo"})
        status, body = self._request("/api/stats")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(body["requests"], 1)
        self.assertIn("halo", body["last_message"])

    def test_agents_endpoint(self):
        status, body = self._request("/api/agents")
        self.assertEqual(status, 200)
        self.assertIsInstance(body["agents"], list)

    def test_agents_run_endpoint_denied_without_yes(self):
        # bridge di test ini dibangun tanpa --yes → eksekusi agent lain DITOLAK
        status, body = self._request("/api/agents/run", {"name": "opencode", "prompt": "halo"})
        self.assertEqual(status, 200)
        self.assertIn("DITOLAK", body["reply"])

    def test_agents_run_endpoint_validation(self):
        status, body = self._request("/api/agents/run", {"name": "", "prompt": ""})
        self.assertEqual(status, 400)
        self.assertIn("wajib", body["error"])


class HistoryRecorderTest(unittest.TestCase):
    """Riwayat percakapan untuk bahan belajar AI."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rec = HistoryRecorder(self.tmp, "sesi-a", source="cli")

    def test_record_writes_jsonl(self):
        self.rec.record(
            "halo", "Halo juga!", provider="deepseek", model="deepseek-chat",
            tokens_in=10, tokens_out=5, cost=0.001,
            tool_calls=[{"name": "list_directory", "arguments": {}, "output": "[...]"}],
            duration_s=1.2,
        )
        self.assertTrue(self.rec.path.exists())
        entries = HistoryRecorder.load_all(self.tmp)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["user"], "halo")
        self.assertEqual(e["provider"], "deepseek")
        self.assertEqual(e["tokens_in"], 10)
        self.assertEqual(len(e["tool_calls"]), 1)
        self.assertEqual(e["source"], "cli")

    def test_summary_empty(self):
        text = HistoryRecorder.summary(self.tmp)
        self.assertIn("belum ada riwayat", text)

    def test_agent_records_history(self):
        """Agent harus menulis ke riwayat saat run() dipanggil."""
        pm = make_manager({"m": "normal"}, "m", ["m"])
        session = Session(self.tmp / "agent-test.jsonl")
        skills = SkillsRegistry(self.tmp / "skills")
        memory = Memory(self.tmp / "MEMORY.md")
        executor = ToolExecutor(cwd=self.tmp)
        rec = HistoryRecorder(self.tmp, "agent-test")
        agent = Agent(None, pm, session, executor, skills, memory, history=rec)
        agent.run("halo dari history")
        entries = HistoryRecorder.load_all(self.tmp)
        self.assertGreaterEqual(len(entries), 1)
        self.assertEqual(entries[0]["user"], "halo dari history")


class AgentsDetectTest(unittest.TestCase):
    """Deteksi CLI agent di mesin (hagema agents)."""

    def test_detect_finds_python(self):
        from hagema.agents import detect_agents, install_command_for
        info = {a.name: a for a in detect_agents(extra=["python3"])}
        self.assertIn("python3", info)
        self.assertTrue(info["python3"].installed)
        # 'python3' tidak dikenal, jadi tidak ada perintah install
        self.assertIsNone(install_command_for("python3"))

    def test_known_agent_install_command(self):
        from hagema.agents import install_command_for
        self.assertIsNotNone(install_command_for("opencode"))
        self.assertIsNotNone(install_command_for("aider"))

    def test_agents_text(self):
        from hagema.agents import agents_text
        text = agents_text()
        self.assertIn("CLI AGENT", text)

    def test_run_cli_agent_unknown(self):
        from hagema.agents import run_cli_agent
        ok, out = run_cli_agent("agent-tidak-ada", "halo")
        self.assertFalse(ok)
        self.assertIn("tidak terpasang", out)

    def test_run_cli_agent_empty(self):
        from hagema.agents import run_cli_agent
        ok, out = run_cli_agent("opencode", "   ")
        self.assertFalse(ok)
        self.assertIn("Pakai", out)

    def test_run_cli_agent_no_one_shot_mode(self):
        """Binary yang terpasang tapi bukan agent dikenal → ditolak dengan pesan jelas."""
        from hagema.agents import run_cli_agent, runnable_agents
        ok, out = run_cli_agent("python3", "halo")
        self.assertFalse(ok)
        self.assertIn("tidak punya mode one-shot", out)
        self.assertIsInstance(runnable_agents(), list)

    def test_runnable_agents_only_installed_with_flags(self):
        from hagema.agents import RUN_FLAGS, runnable_agents
        names = runnable_agents()
        self.assertIsInstance(names, list)
        for n in names:
            self.assertIn(n, RUN_FLAGS)


class AutoMemoryTest(unittest.TestCase):
    """Auto-memory ala Hermes: rekap sesi masuk MEMORY.md otomatis."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.memory = Memory(self.tmp / "MEMORY.md")
        self.session = Session(self.tmp / "s.jsonl")
        self.session.add({"role": "user", "content": "kerjakan tugas"})
        self.session.add({"role": "assistant", "content": "selesai"})

    def test_append_section(self):
        self.memory.append_section("Auto-recap test", "## Konteks\nsesi demo")
        content = self.memory.load()
        self.assertIn("Auto-recap test", content)
        self.assertIn("sesi demo", content)

    def test_auto_remember_with_mock_provider(self):
        pm = make_manager({"m": "normal"}, "m", ["m"])
        ok = self.memory.auto_remember("Auto-recap test", pm.current, self.session)
        self.assertTrue(ok)
        content = self.memory.load()
        self.assertIn("Auto-recap test", content)

    def test_auto_remember_empty_session(self):
        pm = make_manager({"m": "normal"}, "m", ["m"])
        empty = Session(self.tmp / "empty.jsonl")
        ok = self.memory.auto_remember("Auto-recap", pm.current, empty)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
