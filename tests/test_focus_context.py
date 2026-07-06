import unittest

from copilot_voice_shell.focus_context import (
    _conversation_from_title,
    _detect_copilot_cli,
    _focus_is_terminal,
    _looks_like_message,
)


class FocusIsTerminalTests(unittest.TestCase):
    def test_native_terminal_exe(self):
        self.assertTrue(_focus_is_terminal([], "C:/Windows/System32/WindowsTerminal.exe"))
        self.assertTrue(_focus_is_terminal([], "/usr/bin/pwsh"))

    def test_terminal_control_class_in_chain(self):
        chain = [("Document", "", "Xterm-256color"), ("Group", "", "monaco-workbench")]
        self.assertTrue(_focus_is_terminal(chain, "code.exe"))

    def test_editor_focus_is_not_terminal(self):
        chain = [("Edit", "editor", "monaco-editor"), ("Group", "", "monaco-workbench")]
        self.assertFalse(_focus_is_terminal(chain, "code.exe"))


class DetectCopilotCliTests(unittest.TestCase):
    """The confident, pane-level replacement for the old ``bool(session)`` signal:
    only the focused Copilot CLI terminal — not the editor beside it, nor a plain
    shell — should be detected."""

    SUMMARY = "Install Project on Mac and Windows"

    def test_vscode_integrated_terminal_tab_in_ancestry(self):
        # The terminal tab (accessible name == session summary) is in the focused
        # ancestry only when the terminal pane, not the editor, has focus.
        chain = [
            ("Document", "", "xterm"),
            ("TabItem", self.SUMMARY, "tab"),
            ("Group", "", "monaco-workbench"),
        ]
        self.assertTrue(_detect_copilot_cli("VS Code", chain, self.SUMMARY, "code.exe"))

    def test_vscode_editor_focus_is_rejected(self):
        # Editor focused: the terminal tab is NOT in the focused ancestry, so even
        # though the window HAS a resolvable session, we must not claim copilot.
        chain = [
            ("Edit", "main.py", "monaco-editor"),
            ("Group", "", "monaco-workbench"),
        ]
        self.assertFalse(_detect_copilot_cli("VS Code", chain, self.SUMMARY, "code.exe"))

    def test_dedicated_terminal_with_copilot_title(self):
        chain = [("Document", "", "Cascadia")]
        self.assertTrue(
            _detect_copilot_cli("GitHub Copilot", chain, "", "WindowsTerminal.exe")
        )

    def test_plain_shell_without_copilot_is_rejected(self):
        chain = [("Document", "", "consolewindowclass")]
        self.assertFalse(
            _detect_copilot_cli("Windows PowerShell", chain, "", "powershell.exe")
        )

    def test_short_summary_is_ignored(self):
        # A <4 char summary is too weak to match on; avoid false positives.
        chain = [("TabItem", "ab", "tab")]
        self.assertFalse(_detect_copilot_cli("VS Code", chain, "ab", "code.exe"))


class ChatContextTests(unittest.TestCase):
    """Conversation identity + message-bubble recognition for chat apps."""

    def test_conversation_from_teams_title(self):
        self.assertEqual(
            _conversation_from_title("Calendar | Squad-Onboarding | Microsoft Teams"),
            "Calendar / Squad-Onboarding",
        )

    def test_conversation_from_person_title(self):
        self.assertEqual(
            _conversation_from_title("Chat | Ana Mora | Microsoft Teams"),
            "Chat / Ana Mora",
        )

    def test_conversation_bare_app_title_is_empty(self):
        self.assertEqual(_conversation_from_title("Microsoft Teams"), "")

    def test_message_bubble_recognised(self):
        self.assertTrue(
            _looks_like_message("Julie Zhu (M365) 已发送 Hi, could you review this?")
        )
        self.assertTrue(
            _looks_like_message("Adrian Zhang sent Yes I will take a look tomorrow")
        )

    def test_non_message_rejected(self):
        self.assertFalse(_looks_like_message("Send"))
        self.assertFalse(_looks_like_message("聊天"))


if __name__ == "__main__":
    unittest.main()
