import json
import os
import tempfile
import unittest
from pathlib import Path

from bubble_buddy import context_plugins, pi_session
from bubble_buddy.context_plugins import PluginInput
from bubble_buddy.plugins_catalog.pi_web import PiWebPlugin


class PiSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.agent_dir = Path(self._tmp.name) / ".pi" / "agent"
        self.sessions_dir = self.agent_dir / "sessions"
        self._prev_dir = os.environ.get("PI_CODING_AGENT_DIR")
        self._prev_session_dir = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
        os.environ["PI_CODING_AGENT_DIR"] = str(self.agent_dir)
        os.environ.pop("PI_CODING_AGENT_SESSION_DIR", None)
        pi_session._session_cache = (None, 0.0, [])
        pi_session._turn_cache.clear()
        context_plugins._catalog_cache = None

    def tearDown(self) -> None:
        if self._prev_dir is None:
            os.environ.pop("PI_CODING_AGENT_DIR", None)
        else:
            os.environ["PI_CODING_AGENT_DIR"] = self._prev_dir
        if self._prev_session_dir is None:
            os.environ.pop("PI_CODING_AGENT_SESSION_DIR", None)
        else:
            os.environ["PI_CODING_AGENT_SESSION_DIR"] = self._prev_session_dir
        pi_session._session_cache = (None, 0.0, [])
        pi_session._turn_cache.clear()
        context_plugins._catalog_cache = None
        self._tmp.cleanup()

    def _session(self, session_id: str, cwd: str, entries: list[dict] | None = None) -> Path:
        directory = self.sessions_dir / "bucket"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"2026-09-01T07-18-42-542Z_{session_id}.jsonl"
        lines = [
            {
                "type": "session",
                "version": 3,
                "id": session_id,
                "timestamp": "2026-09-01T07:18:42.542Z",
                "cwd": cwd,
            },
            *(entries or []),
        ]
        path.write_text(
            "".join(json.dumps(item) + "\n" for item in lines),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _entry(entry_id: str, parent_id: str | None, role: str, content: object) -> dict:
        return {
            "type": "message",
            "id": entry_id,
            "parentId": parent_id,
            "timestamp": "2026-09-01T07:18:42.542Z",
            "message": {"role": role, "content": content},
        }

    def test_resolve_matches_title_project_name(self):
        other = self._session("session-other", "C:/repo/other")
        expected = self._session("session-demo", "C:/repo/demo")
        os.utime(other, (1, 1))
        os.utime(expected, (2, 2))
        pi_session._session_cache = (None, 0.0, [])

        match = pi_session.resolve_session("demo - pi web - Google Chrome")

        self.assertIsNotNone(match)
        self.assertEqual(match.id, "session-demo")
        self.assertEqual(match.path, expected)
        self.assertTrue(match.exact)

    def test_resolve_can_fall_back_to_latest(self):
        older = self._session("session-old", "C:/repo/old")
        latest = self._session("session-new", "C:/repo/new")
        os.utime(older, (1, 1))
        os.utime(latest, (2, 2))
        pi_session._session_cache = (None, 0.0, [])

        match = pi_session.resolve_session("pi web", allow_latest=True)

        self.assertIsNotNone(match)
        self.assertEqual(match.id, "session-new")
        self.assertFalse(match.exact)

    def test_resolve_prefers_session_id_from_browser_url(self):
        self._session("session-old", "C:/repo/demo")
        expected = self._session(
            "01a0614c-5036-71bc-9a5e-3d5ccf27b041", "C:/repo/demo"
        )
        pi_session._session_cache = (None, 0.0, [])

        match = pi_session.resolve_session(
            "demo - pi web",
            allow_latest=True,
            browser_url="https://pi.example/session/01a0614c-5036-71bc-9a5e-3d5ccf27b041",
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.path, expected)
        self.assertTrue(match.exact)

    def test_resolve_does_not_guess_between_same_workspace_sessions(self):
        self._session("session-one", "C:/repo/demo")
        self._session("session-two", "C:/repo/demo")
        pi_session._session_cache = (None, 0.0, [])

        match = pi_session.resolve_session("demo - pi web", allow_latest=True)

        self.assertIsNone(match)

    def test_recent_turns_uses_active_branch_and_text_blocks_only(self):
        path = self._session(
            "session-demo",
            "C:/repo/demo",
            [
                self._entry("u1", None, "user", [{"type": "text", "text": "first"}]),
                self._entry(
                    "a1",
                    "u1",
                    "assistant",
                    [
                        {"type": "thinking", "thinking": "hidden"},
                        {"type": "text", "text": "first answer"},
                    ],
                ),
                self._entry(
                    "branch",
                    "a1",
                    "user",
                    [{"type": "text", "text": "stale branch"}],
                ),
                self._entry("u2", "a1", "user", [{"type": "text", "text": "second"}]),
                self._entry(
                    "tool",
                    "u2",
                    "toolResult",
                    [{"type": "text", "text": "ignored tool"}],
                ),
                self._entry(
                    "a2",
                    "tool",
                    "assistant",
                    [{"type": "text", "text": "second answer"}],
                ),
            ],
        )

        turns = pi_session.recent_turns(path, limit=4)

        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0].user_message, "first")
        self.assertEqual(turns[0].assistant_response, "first answer")
        self.assertEqual(turns[1].user_message, "second")
        self.assertEqual(turns[1].assistant_response, "second answer")

    def test_plugin_detects_and_formats_pi_transcript(self):
        self._session(
            "session-demo",
            "C:/repo/copilot-voice-shell",
            [
                self._entry(
                    "u1",
                    None,
                    "user",
                    [{"type": "text", "text": "bridge this"}],
                ),
                self._entry(
                    "a1",
                    "u1",
                    "assistant",
                    [{"type": "text", "text": "implemented"}],
                ),
            ],
        )
        ctx = PluginInput(
            title="copilot-voice-shell - pi web - Google Chrome",
            app_name="Google Chrome",
            exe_path="chrome.exe",
            browser_url="https://pi.example/session/session-demo",
            ancestry=(("Document", "pi web", "Chrome_RenderWidgetHostHWND"),),
        )

        result = PiWebPlugin().extract(ctx)

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "pi_web")
        self.assertIn("我：bridge this", result.text)
        self.assertIn("Pi：implemented", result.text)
        self.assertTrue(result.resource.endswith(".jsonl"))

    def test_plugin_rejects_regular_browser_tab(self):
        self._session("session-demo", "C:/repo/copilot-voice-shell")
        ctx = PluginInput(
            title="docs - Google Chrome",
            app_name="Google Chrome",
            exe_path="chrome.exe",
            ancestry=(("Document", "Readme", "Chrome_RenderWidgetHostHWND"),),
        )
        self.assertFalse(PiWebPlugin().matches(ctx))


if __name__ == "__main__":
    unittest.main()
