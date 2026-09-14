import json
import os
import tempfile
import unittest
from pathlib import Path

from bubble_buddy import codex_session, context_plugins
from bubble_buddy.context_plugins import PluginInput
from bubble_buddy.plugins_catalog.codex_cli import CodexCliPlugin


class CodexSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / ".codex"
        self._previous_home = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.home)
        codex_session._rollout_cache = (None, 0.0, [])
        codex_session._turn_cache.clear()

    def tearDown(self) -> None:
        if self._previous_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self._previous_home
        codex_session._rollout_cache = (None, 0.0, [])
        codex_session._turn_cache.clear()
        context_plugins._catalog_cache = None
        self._tmp.cleanup()

    def _rollout(
        self,
        session_id: str,
        cwd: str,
        events: list[dict] | None = None,
        nested_meta: bool = False,
    ) -> Path:
        directory = self.home / "sessions" / "2026" / "08" / "18"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"rollout-2026-08-18T10-00-00-{session_id}.jsonl"
        meta = {"id": session_id, "cwd": cwd}
        payload = {"meta": meta} if nested_meta else meta
        lines = [{"type": "session_meta", "payload": payload}, *(events or [])]
        path.write_text(
            "".join(json.dumps(item) + "\n" for item in lines),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _event(event_type: str, message: str, phase: str | None = None) -> dict:
        payload = {"type": event_type, "message": message}
        if phase is not None:
            payload["phase"] = phase
        return {"type": "event_msg", "payload": payload}

    def test_resolve_matches_focused_workspace(self):
        expected = self._rollout("session-demo", "C:/repo/demo", nested_meta=True)
        self._rollout("session-other", "C:/repo/other")

        match = codex_session.resolve_session(
            "main.py - demo - Visual Studio Code"
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.id, "session-demo")
        self.assertEqual(match.path, expected)
        self.assertTrue(match.exact)

    def test_resolve_can_fall_back_to_latest_for_dedicated_terminal(self):
        older = self._rollout("session-old", "C:/repo/old")
        latest = self._rollout("session-new", "C:/repo/new")
        os.utime(older, (1, 1))
        os.utime(latest, (2, 2))

        match = codex_session.resolve_session("Codex", allow_latest=True)

        self.assertIsNotNone(match)
        self.assertEqual(match.id, "session-new")
        self.assertFalse(match.exact)

    def test_recent_turns_ignores_commentary_and_limits(self):
        path = self._rollout(
            "session-demo",
            "C:/repo/demo",
            [
                self._event("user_message", "first"),
                self._event("agent_message", "working", "commentary"),
                self._event("agent_message", "first answer", "final_answer"),
                self._event("user_message", "second"),
                self._event("agent_message", "second answer"),
            ],
        )

        turns = codex_session.recent_turns(path, limit=1)

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].user_message, "second")
        self.assertEqual(turns[0].assistant_response, "second answer")

    def test_plugin_detects_and_formats_codex_transcript(self):
        self._rollout(
            "session-demo",
            "C:/repo/demo",
            [
                self._event("user_message", "bridge this"),
                self._event("agent_message", "implemented"),
            ],
        )
        ctx = PluginInput(
            title="main.py - demo - Visual Studio Code",
            exe_path="code.exe",
            ancestry=(
                (
                    "Edit",
                    "Terminal 2, codex - Codex Use Alt+F1 for help",
                    "xterm-helper-textarea",
                ),
            ),
        )

        result = CodexCliPlugin().extract(ctx)

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "codex_cli")
        self.assertIn("我：bridge this", result.text)
        self.assertIn("Codex：implemented", result.text)
        self.assertTrue(result.resource.endswith(".jsonl"))

    def test_plugin_rejects_plain_terminal(self):
        self._rollout("session-demo", "C:/repo/demo")
        ctx = PluginInput(
            title="main.py - demo - Visual Studio Code",
            exe_path="code.exe",
            ancestry=(("Edit", "Terminal 1, pwsh", "xterm-helper-textarea"),),
        )
        self.assertFalse(CodexCliPlugin().matches(ctx))


if __name__ == "__main__":
    unittest.main()
