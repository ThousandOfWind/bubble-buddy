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
        self.assertNotIn("请执行下面的语音指令", result)
        self.assertIn("Copilot", result)
        self.assertIn("skill", result)
        self.assertIn("streaming", result)

    def test_cleanup_dictation_normalizes_terms(self) -> None:
        self.assertEqual(cleanup_dictation("cloud code 和 github api"), "Claude Code 和 GitHub API")

    def test_cleanup_removes_prompt_prefix_and_repetition(self) -> None:
        self.assertEqual(
            cleanup_dictation("请执行下面的语音指令：默认打开默认打开 dashboard dashboard"),
            "默认打开 dashboard",
        )

    def test_cleanup_keeps_wrong_scripts_by_default(self) -> None:
        self.assertEqual(cleanup_dictation("优化 Copilot 세션 แล้วทดสอบ"), "优化 Copilot 세션 แล้วทดสอบ")

    def test_cleanup_reduces_single_char_stutter(self) -> None:
        self.assertEqual(cleanup_dictation("我希望你你你继续优化"), "我希望你继续优化")

    def test_polish_adds_sentence_punctuation(self) -> None:
        self.assertTrue(polish_text("默认打开 dashboard", "copilot").endswith("。"))
        self.assertTrue(polish_text("你能不能默认打开 dashboard", "copilot").endswith("？"))

    def test_app_to_polish_mode_mapping(self) -> None:
        from copilot_voice_shell.polish import map_app_to_polish_mode
        self.assertEqual(map_app_to_polish_mode("VS Code", "com.microsoft.VSCode"), "dev")
        self.assertEqual(map_app_to_polish_mode("iTerm2", "com.googlecode.iterm2"), "dev")
        self.assertEqual(map_app_to_polish_mode("WeChat", "com.tencent.xinWeChat"), "im")
        self.assertEqual(map_app_to_polish_mode("Lark", "com.electron.lark"), "im")
        self.assertEqual(map_app_to_polish_mode("Notion", "notion.id"), "notes")
        self.assertEqual(map_app_to_polish_mode("Outlook", "com.microsoft.Outlook"), "email")
        self.assertEqual(map_app_to_polish_mode("Safari", "com.apple.Safari"), "browser")
        self.assertEqual(map_app_to_polish_mode("Chrome", "com.google.Chrome"), "browser")
        self.assertEqual(map_app_to_polish_mode("UnknownApp"), "copilot")

    def test_polish_modes_formatting(self) -> None:
        # Dev and browser modes should NOT end with automatic sentence punctuation in rules engine
        self.assertEqual(polish_text("git status", "dev"), "git status")
        self.assertEqual(polish_text("python handle json", "browser"), "python handle json")
        # Other modes should have standard punctuation in rules engine
        self.assertTrue(polish_text("好的我马上去办", "im").endswith("。"))
        self.assertTrue(polish_text("这个是会议纪要", "notes").endswith("。"))


if __name__ == "__main__":
    unittest.main()
