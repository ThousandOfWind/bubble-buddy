from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from copilot_voice_shell.cli import (
    apply_replacements,
    load_replacements,
    merge_segment_text,
    parse_replacement_pair,
)


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


if __name__ == "__main__":
    unittest.main()
