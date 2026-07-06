import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from copilot_voice_shell import context_plugins, copilot_session
from copilot_voice_shell.context_plugins import (
    CopilotCliPlugin,
    PluginInput,
    PluginResult,
    extract_all,
    get_plugins,
)


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
        self._prev_plugins = os.environ.get("CVS_PLUGINS_DIR")
        os.environ["CVS_PLUGINS_DIR"] = str(Path(self._tmp.name) / "plugins")
        context_plugins._user_cache = None

    def tearDown(self) -> None:
        context_plugins._user_cache = None
        if self._prev is None:
            os.environ.pop("COPILOT_HOME", None)
        else:
            os.environ["COPILOT_HOME"] = self._prev
        if self._prev_plugins is None:
            os.environ.pop("CVS_PLUGINS_DIR", None)
        else:
            os.environ["CVS_PLUGINS_DIR"] = self._prev_plugins
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


class CopilotCliPluginTests(_TempHomeMixin):
    def test_matches_requires_copilot_and_session(self):
        plugin = CopilotCliPlugin()
        self.assertTrue(
            plugin.matches(PluginInput(copilot_cli=True, session_id="sess-1"))
        )
        self.assertFalse(
            plugin.matches(PluginInput(copilot_cli=True, session_id=""))
        )
        self.assertFalse(
            plugin.matches(PluginInput(copilot_cli=False, session_id="sess-1"))
        )

    def test_extract_formats_transcript(self):
        _make_store(
            self.home,
            "sess-1",
            [(0, "hello there", "hi back"), (1, "second", "reply two")],
        )
        result = CopilotCliPlugin().extract(
            PluginInput(copilot_cli=True, session_id="sess-1")
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "copilot_cli")
        self.assertIn("hello there", result.text)
        self.assertIn("Copilot：reply two", result.text)

    def test_extract_none_when_empty(self):
        _make_store(self.home, "sess-1", [])
        self.assertIsNone(
            CopilotCliPlugin().extract(
                PluginInput(copilot_cli=True, session_id="sess-1")
            )
        )


class RegistryTests(_TempHomeMixin):
    def test_extract_all_includes_copilot_result(self):
        _make_store(self.home, "sess-1", [(0, "q", "a")])
        results = extract_all(PluginInput(copilot_cli=True, session_id="sess-1"))
        self.assertEqual([r.name for r in results], ["copilot_cli"])

    def test_extract_all_survives_broken_plugin(self):
        class Boom:
            name = "boom"

            def matches(self, ctx):
                raise RuntimeError("nope")

            def extract(self, ctx):
                raise RuntimeError("nope")

        context_plugins._BUILTIN.append(Boom())
        try:
            # Must not raise despite the broken plugin.
            results = extract_all(PluginInput(app_name="x"))
            self.assertEqual(results, [])
        finally:
            context_plugins._BUILTIN.pop()

    def test_disabled_plugin_excluded(self):
        import copilot_voice_shell.config as cfg

        original = cfg.load_config
        cfg.load_config = lambda: {"disabled_context_plugins": ["copilot_cli"]}
        try:
            names = [p.name for p in get_plugins(refresh=True)]
            self.assertNotIn("copilot_cli", names)
        finally:
            cfg.load_config = original

    def test_user_plugin_discovered(self):
        plugins_dir = Path(os.environ["CVS_PLUGINS_DIR"])
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "my_plugin.py").write_text(
            "from copilot_voice_shell.context_plugins import PluginResult\n"
            "class P:\n"
            "    name = 'my_plugin'\n"
            "    def matches(self, ctx): return ctx.app_name == 'demo'\n"
            "    def extract(self, ctx): return PluginResult('my_plugin', 'Demo', 'ctx')\n"
            "PLUGIN = P()\n",
            encoding="utf-8",
        )
        results = extract_all(PluginInput(app_name="demo"))
        self.assertIn("my_plugin", [r.name for r in results])


if __name__ == "__main__":
    unittest.main()
