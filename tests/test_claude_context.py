import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bubble_buddy import claude_session
from bubble_buddy.context_plugins import PluginInput
from bubble_buddy.plugins_catalog.claude_code import ClaudeCodePlugin


def _make_transcript(home: Path, project: str = "demo") -> Path:
    path = home / "projects" / f"C--repo-{project}" / "session.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "type": "user",
            "cwd": f"C:/repo/{project}",
            "message": {"content": "fix the parser"},
        },
        {
            "type": "assistant",
            "cwd": f"C:/repo/{project}",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "hidden"},
                    {"type": "text", "text": "I updated parser.py"},
                ]
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows),
        encoding="utf-8",
    )
    return path


class ClaudeSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / ".claude"
        self.previous = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.home)
        claude_session._file_cache = (None, 0.0, [])
        claude_session._turn_cache.clear()

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.previous
        self.tmp.cleanup()

    def test_resolves_workspace_and_reads_text_blocks(self):
        transcript = _make_transcript(self.home)
        match = claude_session.resolve_session("main.py - demo - Visual Studio Code")
        self.assertIsNotNone(match)
        self.assertEqual(match.path, transcript)
        self.assertTrue(match.exact)
        turns = claude_session.recent_turns(transcript)
        self.assertEqual(turns[0].user_message, "fix the parser")
        self.assertEqual(turns[0].assistant_response, "I updated parser.py")

    def test_plugin_supports_vscode_panel(self):
        transcript = _make_transcript(self.home)
        ctx = PluginInput(
            title="main.py - demo - Visual Studio Code",
            exe_path="code.exe",
            ancestry=(("Edit", "Ask Claude Code", "chat-input"),),
        )
        result = ClaudeCodePlugin().extract(ctx)
        self.assertIsNotNone(result)
        self.assertEqual(result.resource, str(transcript))
        self.assertIn("Claude：I updated parser.py", result.text)

    def test_plugin_supports_standalone_ui_with_latest_session(self):
        _make_transcript(self.home)
        ctx = PluginInput(
            title="Claude",
            app_name="Claude",
            exe_path=r"C:\Program Files\Claude\Claude.exe",
        )
        self.assertIsNotNone(ClaudeCodePlugin().extract(ctx))

    def test_plugin_rejects_regular_vscode_editor(self):
        _make_transcript(self.home)
        ctx = PluginInput(
            title="main.py - demo - Visual Studio Code",
            exe_path="code.exe",
            ancestry=(("Edit", "main.py", "monaco-editor"),),
        )
        with mock.patch.object(
            claude_session, "resolve_session", wraps=claude_session.resolve_session
        ) as resolve:
            self.assertIsNone(ClaudeCodePlugin().extract(ctx))
            resolve.assert_not_called()
