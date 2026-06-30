from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from copilot_voice_shell.cli import (
    apply_replacements,
    load_replacements,
    merge_segment_text,
    normalize_hotkey,
    parse_replacement_pair,
    resolve_send_text,
)
from copilot_voice_shell.polish import cleanup_dictation, polish_text


class CliHelpersTest(unittest.TestCase):
    def test_parse_replacement_pair(self) -> None:
        self.assertEqual(parse_replacement_pair("Scale=skill"), ("Scale", "skill"))

    def test_apply_replacements(self) -> None:
        result = apply_replacements(
            "I want a new Scale for cloud code in copilot",
            {"Scale": "skill", "cloud code": "Claude Code", "copilot": "Copilot"},
        )
        self.assertEqual(result, "I want a new skill for Claude Code in Copilot")

    def test_merge_segment_text(self) -> None:
        self.assertEqual(merge_segment_text([" hello ", "", "world  "]), "hello world")

    def test_load_replacements_from_file_and_flags(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "replacements.json"
            path.write_text('{"Scale": "skill", "copilot": "Copilot"}', encoding="utf-8")
            loaded = load_replacements(path, ["github=GitHub"])

        self.assertEqual(
            loaded,
            {"Scale": "skill", "copilot": "Copilot", "github": "GitHub"},
        )

    def test_resolve_send_text_from_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "prompt.txt"
            path.write_text("hello copilot\n", encoding="utf-8")
            self.assertEqual(resolve_send_text(None, path), "hello copilot")

    def test_normalize_hotkey(self) -> None:
        self.assertEqual(normalize_hotkey("cmd+shift+space"), "<cmd>+<shift>+<space>")

    def test_normalize_hotkey_with_letters(self) -> None:
        self.assertEqual(normalize_hotkey("ctrl+alt+r"), "<ctrl>+<alt>+r")

    def test_polish_copilot_instruction(self) -> None:
        result = polish_text("呃 帮我修一下 copilot skill 的 streaming", "copilot")
        self.assertIn("Copilot", result)
        self.assertIn("skill", result)
        self.assertIn("streaming", result)

    def test_cleanup_dictation_normalizes_terms(self) -> None:
        self.assertEqual(cleanup_dictation("cloud code 和 github api"), "Claude Code 和 GitHub API")


if __name__ == "__main__":
    unittest.main()
