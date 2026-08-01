"""Unit test inti hagema-agent: loop, tool calling, failover + rekap."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hagema.agent import Agent  # noqa: E402
from hagema.config import Config  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
