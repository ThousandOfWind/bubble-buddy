import unittest

from bubble_buddy.polish import map_app_to_polish_mode, resolve_polish_mode


class CopilotCliPolishModeTests(unittest.TestCase):
    """A Copilot CLI session focused in a VS Code terminal should use the gentle
    ``copilot`` mode, not the aggressive ``dev`` mode that rewrites text as terse
    commands. Only a clearly-classified editor focus stays on ``dev``."""

    def test_copilot_session_terminal_maps_to_copilot(self):
        self.assertEqual(
            map_app_to_polish_mode("Code", "", sub_kind="terminal", copilot_session=True),
            "copilot",
        )

    def test_copilot_session_unknown_subkind_maps_to_copilot(self):
        # xterm canvas often defeats classification (sub_kind == "").
        self.assertEqual(
            map_app_to_polish_mode("Code", "", sub_kind="", copilot_session=True),
            "copilot",
        )

    def test_copilot_session_flag_overrides_app_keywords(self):
        # copilot_session is now the CONFIDENT pane-level flag (FocusInfo.copilot_cli),
        # only ever set when the terminal pane — not the editor — has focus. So it
        # unconditionally wins; the old ``sub_kind != "editor"`` guard moved upstream
        # into focus_context._detect_copilot_cli (covered in test_focus_context.py).
        self.assertEqual(
            map_app_to_polish_mode("Code", "", sub_kind="editor", copilot_session=True),
            "copilot",
        )

    def test_plain_terminal_without_session_stays_dev(self):
        self.assertEqual(
            map_app_to_polish_mode("Code", "", sub_kind="terminal", copilot_session=False),
            "dev",
        )

    def test_resolve_auto_threads_copilot_session(self):
        self.assertEqual(
            resolve_polish_mode("auto", "Code", "", sub_kind="terminal", copilot_session=True),
            "copilot",
        )

    def test_resolve_non_auto_passthrough(self):
        # A fixed mode is returned unchanged regardless of focus hints.
        self.assertEqual(
            resolve_polish_mode("copilot", "Code", "", sub_kind="editor", copilot_session=True),
            "copilot",
        )


if __name__ == "__main__":
    unittest.main()
