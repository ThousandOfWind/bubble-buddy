import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bubble_buddy import context_plugins, copilot_session, focus_context
from bubble_buddy.context_plugins import (
    PluginInput,
    PluginResult,
    extract_all,
    get_plugins,
    install_plugin,
    uninstall_plugin,
    enabled_names,
)
from bubble_buddy.plugins_catalog.copilot_cli import CopilotCliPlugin


def _make_store(home: Path, session_id: str, turns: list[tuple[int, str, str]]) -> None:
    """Create a minimal Copilot session-store.db with the given turns."""
    home.mkdir(parents=True, exist_ok=True)
    db = home / "session-store.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, repository TEXT,
            host_type TEXT, branch TEXT, summary TEXT,
            created_at TEXT, updated_at TEXT);
        CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, turn_index INTEGER NOT NULL,
            user_message TEXT, assistant_response TEXT, timestamp TEXT,
            UNIQUE(session_id, turn_index));
        """
    )
    con.execute(
        "INSERT INTO sessions (id, cwd, summary) VALUES (?, ?, ?)",
        (session_id, "C:/repo/demo", "Demo Session"),
    )
    con.executemany(
        "INSERT INTO turns (session_id, turn_index, user_message, assistant_response) "
        "VALUES (?, ?, ?, ?)",
        [(session_id, ti, um, ar) for ti, um, ar in turns],
    )
    con.commit()
    con.close()


class _TempHomeMixin(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / ".copilot"
        self._prev = os.environ.get("COPILOT_HOME")
        os.environ["COPILOT_HOME"] = str(self.home)
        # User plugins live elsewhere; keep discovery empty and un-cached.
        self._prev_plugins = os.environ.get("BB_PLUGINS_DIR")
        os.environ["BB_PLUGINS_DIR"] = str(Path(self._tmp.name) / "plugins")
        context_plugins._user_cache = None
        context_plugins._catalog_cache = None

    def tearDown(self) -> None:
        context_plugins._user_cache = None
        context_plugins._catalog_cache = None
        if self._prev is None:
            os.environ.pop("COPILOT_HOME", None)
        else:
            os.environ["COPILOT_HOME"] = self._prev
        if self._prev_plugins is None:
            os.environ.pop("BB_PLUGINS_DIR", None)
        else:
            os.environ["BB_PLUGINS_DIR"] = self._prev_plugins
        self._tmp.cleanup()


class RecentTurnsTests(_TempHomeMixin):
    def test_recent_turns_oldest_first_and_limited(self):
        _make_store(
            self.home,
            "sess-1",
            [
                (0, "first q", "first a"),
                (1, "second q", "second a"),
                (2, "third q", "third a"),
            ],
        )
        turns = copilot_session.recent_turns("sess-1", limit=2)
        self.assertEqual([t.turn_index for t in turns], [1, 2])
        self.assertEqual(turns[0].user_message, "second q")

    def test_recent_turns_missing_session(self):
        _make_store(self.home, "sess-1", [(0, "q", "a")])
        self.assertEqual(copilot_session.recent_turns("nope"), [])

    def test_recent_turns_no_store(self):
        self.assertEqual(copilot_session.recent_turns("sess-1"), [])

    def test_recent_turns_blank_id(self):
        self.assertEqual(copilot_session.recent_turns(""), [])


class _InterpretMixin(_TempHomeMixin):
    """Stubs the two interpretation seams the Copilot plugin owns (session
    resolution + confident pane detection) so tests exercise the plugin's own
    logic — sliding-window formatting — from purely native PluginInput."""

    def setUp(self) -> None:
        super().setUp()
        self._prev_resolve = copilot_session.resolve_session
        self._prev_detect = focus_context.detect_copilot_cli

    def tearDown(self) -> None:
        copilot_session.resolve_session = self._prev_resolve
        focus_context.detect_copilot_cli = self._prev_detect
        super().tearDown()

    def _stub(self, session_id: str, is_cli: bool = True) -> None:
        copilot_session.resolve_session = lambda title, blob="": (
            copilot_session.SessionMatch(id=session_id, summary="Demo Session")
            if session_id
            else None
        )
        focus_context.detect_copilot_cli = lambda *a, **k: is_cli


class CopilotCliPluginTests(_InterpretMixin):
    def test_matches_requires_copilot_pane_and_session(self):
        plugin = CopilotCliPlugin()
        # Native input only — the plugin resolves everything itself.
        ctx = PluginInput(title="repo — Visual Studio Code", exe_path="code.exe")

        self._stub("sess-1", is_cli=True)
        self.assertTrue(plugin.matches(ctx))

        self._stub("sess-1", is_cli=False)  # resolvable session but not the CLI pane
        self.assertFalse(plugin.matches(ctx))

        self._stub("", is_cli=True)  # confident pane but no session to load
        self.assertFalse(plugin.matches(ctx))

    def test_extract_formats_transcript(self):
        _make_store(
            self.home,
            "sess-1",
            [(0, "hello there", "hi back"), (1, "second", "reply two")],
        )
        self._stub("sess-1", is_cli=True)
        result = CopilotCliPlugin().extract(PluginInput(title="x"))
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "copilot_cli")
        self.assertIn("hello there", result.text)
        self.assertIn("Copilot：reply two", result.text)

    def test_extract_none_without_session(self):
        _make_store(self.home, "sess-1", [(0, "q", "a")])
        self._stub("", is_cli=True)
        self.assertIsNone(CopilotCliPlugin().extract(PluginInput(title="x")))

    def test_extract_none_when_no_turns(self):
        _make_store(self.home, "sess-1", [])
        self._stub("sess-1", is_cli=True)
        self.assertIsNone(CopilotCliPlugin().extract(PluginInput(title="x")))

    def test_sliding_window_drops_whole_oldest_turns(self):
        long_reply = "补" * 400  # each turn far exceeds the char budget
        _make_store(
            self.home,
            "sess-1",
            [(i, f"q{i}", long_reply) for i in range(6)],
        )
        self._stub("sess-1", is_cli=True)
        result = CopilotCliPlugin(max_turns=6, per_message=160, max_chars=400).extract(
            PluginInput(title="x")
        )
        self.assertIsNotNone(result)
        # The newest turn is always kept; the oldest fall off to fit the budget.
        self.assertIn("q5", result.text)
        self.assertNotIn("q0", result.text)
        # No mid-message cut of a kept turn: every kept reply is clipped to 160.
        self.assertNotIn("补" * 200, result.text)


class RegistryTests(_InterpretMixin):
    def _patch_config(self, cfg_dict: dict) -> None:
        import bubble_buddy.config as cfg

        self.addCleanup(setattr, cfg, "load_config", cfg.load_config)
        cfg.load_config = lambda: dict(cfg_dict)

    def test_extract_all_includes_copilot_result(self):
        _make_store(self.home, "sess-1", [(0, "q", "a")])
        self._patch_config({"enabled_plugins": ["copilot_cli"]})
        self._stub("sess-1", is_cli=True)
        results = extract_all(PluginInput(title="x"))
        self.assertEqual([r.name for r in results], ["copilot_cli"])

    def test_extract_all_survives_broken_user_plugin(self):
        plugins_dir = Path(os.environ["BB_PLUGINS_DIR"])
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "boom.py").write_text(
            "class P:\n"
            "    name = 'boom'\n"
            "    def matches(self, ctx): raise RuntimeError('nope')\n"
            "    def extract(self, ctx): raise RuntimeError('nope')\n"
            "PLUGIN = P()\n",
            encoding="utf-8",
        )
        self._patch_config({"enabled_plugins": []})
        # Must not raise despite the broken plugin, and boom contributes nothing.
        results = extract_all(PluginInput(app_name="x"))
        self.assertNotIn("boom", [r.name for r in results])

    def test_enabled_names_defaults_to_default_enabled(self):
        self._patch_config({})  # no enabled_plugins key -> catalog defaults
        self.assertIn("copilot_cli", enabled_names(refresh=True))
        self.assertNotIn("browser_page", enabled_names(refresh=True))

    def test_install_and_uninstall_persist(self):
        saved: dict = {}

        import bubble_buddy.config as cfg

        self.addCleanup(setattr, cfg, "load_config", cfg.load_config)
        self.addCleanup(setattr, cfg, "save_config", cfg.save_config)
        cfg.load_config = lambda: dict(saved)
        cfg.save_config = lambda patch: saved.update(patch)

        self.assertTrue(install_plugin("browser_page"))
        self.assertIn("browser_page", saved.get("enabled_plugins", []))
        self.assertTrue(uninstall_plugin("browser_page"))
        self.assertNotIn("browser_page", saved.get("enabled_plugins", []))
        self.assertFalse(install_plugin("does_not_exist"))

    def test_user_plugin_discovered(self):
        plugins_dir = Path(os.environ["BB_PLUGINS_DIR"])
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "my_plugin.py").write_text(
            "from bubble_buddy.context_plugins import PluginResult\n"
            "class P:\n"
            "    name = 'my_plugin'\n"
            "    def matches(self, ctx): return ctx.app_name == 'demo'\n"
            "    def extract(self, ctx): return PluginResult('my_plugin', 'Demo', 'ctx')\n"
            "PLUGIN = P()\n",
            encoding="utf-8",
        )
        self._patch_config({"enabled_plugins": []})
        results = extract_all(PluginInput(app_name="demo"))
        self.assertIn("my_plugin", [r.name for r in results])


if __name__ == "__main__":
    unittest.main()
