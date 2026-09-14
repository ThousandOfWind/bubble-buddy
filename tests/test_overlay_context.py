import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bubble_buddy.qt_overlay import (
    FocusTarget,
    _agent_context_hits,
    _agent_display_name,
    _agent_slug,
    _browser_session_hint,
    _has_agent_context,
)


class OverlayContextHelpersTest(unittest.TestCase):
    def test_infers_pi_agent_from_plugin_hit(self):
        target = FocusTarget(
            system="Windows",
            name="Microsoft Edge",
            title="copilot-voice-shell - Pi Web",
            plugins=(
                SimpleNamespace(
                    name="pi_web",
                    label="pi 会话记录",
                    text="我：hello\nPi：world",
                    resource="demo.jsonl",
                ),
            ),
        )
        self.assertEqual(_agent_slug(target), "pi")
        self.assertEqual(_agent_display_name(target), "Pi")
        self.assertEqual(_agent_context_hits(target), ["pi 会话记录"])
        self.assertTrue(_has_agent_context(target))

    def test_explicit_agent_wins_without_plugin(self):
        target = FocusTarget(system="Windows", name="Cursor", coding_agent="cursor")
        self.assertEqual(_agent_slug(target), "cursor")
        self.assertEqual(_agent_display_name(target), "Cursor")
        self.assertTrue(_has_agent_context(target))

    def test_infers_copilot_from_flag(self):
        target = FocusTarget(system="Windows", name="VS Code", copilot_cli=True)
        self.assertEqual(_agent_slug(target), "copilot")
        self.assertEqual(_agent_display_name(target), "Copilot")
        self.assertTrue(_has_agent_context(target))

    def test_browser_hint_exposes_uuid_not_url_query(self):
        target = FocusTarget(
            system="Windows",
            browser_url=(
                "https://example.test/?token=secret&session="
                "01a05bd5-a26e-7ac5-bb1e-6621633631f4"
            ),
        )
        self.assertEqual(
            _browser_session_hint(target),
            "01a05bd5-a26e-7ac5-bb1e-6621633631f4",
        )


if __name__ == "__main__":
    unittest.main()
