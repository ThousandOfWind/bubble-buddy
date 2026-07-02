import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from copilot_voice_shell import copilot_session as cs


def _make_store(home: Path, rows):
    (home / "ide").mkdir(parents=True, exist_ok=True)
    db = home / "session-store.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE sessions (id TEXT, cwd TEXT, repository TEXT, "
        "branch TEXT, summary TEXT, updated_at INTEGER)"
    )
    con.executemany(
        "INSERT INTO sessions (id, cwd, repository, branch, summary, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()
    return db


def _make_lock(home: Path, workspace: str):
    (home / "ide").mkdir(parents=True, exist_ok=True)
    (home / "ide" / "w.lock").write_text(
        json.dumps({"workspaceFolders": [workspace]}), encoding="utf-8"
    )


class FolderFromTitleTest(unittest.TestCase):
    def test_extracts_workspace_folder(self):
        self.assertEqual(
            cs._folder_from_title("file.py - my-proj - Visual Studio Code"),
            "my-proj",
        )

    def test_folder_only_title(self):
        self.assertEqual(
            cs._folder_from_title("my-proj - Visual Studio Code"), "my-proj"
        )

    def test_strips_workspace_suffix(self):
        self.assertEqual(
            cs._folder_from_title("x - my-proj (Workspace) - Cursor"), "my-proj"
        )

    def test_empty(self):
        self.assertEqual(cs._folder_from_title(""), "")
        self.assertEqual(cs._folder_from_title("Visual Studio Code"), "")


class ResolveSessionTest(unittest.TestCase):
    def _resolve(self, home, title, blob=""):
        with mock.patch.object(cs, "copilot_home", return_value=home):
            return cs.resolve_session(title, blob)

    def test_exact_summary_match(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            ws = str(home / "my-proj")
            _make_store(
                home,
                [
                    ("s1", ws, "o/my-proj", "main", "Build the thing", 100),
                    ("s2", ws, "o/my-proj", "feat", "Fix the bug", 200),
                ],
            )
            _make_lock(home, ws)
            m = self._resolve(
                home,
                "my-proj - Visual Studio Code",
                "Terminal 1, Build the thing",
            )
            self.assertIsNotNone(m)
            self.assertEqual(m.id, "s1")
            self.assertTrue(m.exact)

    def test_fallback_to_most_recent(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            ws = str(home / "my-proj")
            _make_store(
                home,
                [
                    ("s1", ws, "o/my-proj", "main", "Build the thing", 100),
                    ("s2", ws, "o/my-proj", "feat", "Fix the bug", 200),
                ],
            )
            _make_lock(home, ws)
            m = self._resolve(home, "my-proj - Visual Studio Code", "nothing")
            self.assertIsNotNone(m)
            self.assertEqual(m.id, "s2")  # highest updated_at
            self.assertFalse(m.exact)

    def test_prefers_longest_summary_match(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            ws = str(home / "my-proj")
            _make_store(
                home,
                [
                    # shorter summary is a substring of the longer, and is more recent
                    ("s1", ws, "o/my-proj", "main", "Fix", 300),
                    ("s2", ws, "o/my-proj", "feat", "Fix parser bug", 100),
                ],
            )
            _make_lock(home, ws)
            m = self._resolve(
                home, "my-proj - Visual Studio Code", "Terminal: Fix parser bug"
            )
            self.assertIsNotNone(m)
            self.assertEqual(m.id, "s2")
            self.assertTrue(m.exact)

    def test_ignores_too_short_summary(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            ws = str(home / "my-proj")
            _make_store(
                home,
                [("s1", ws, "o/my-proj", "main", "ab", 300)],
            )
            _make_lock(home, ws)
            m = self._resolve(
                home, "my-proj - Visual Studio Code", "Terminal: abcdef"
            )
            self.assertIsNotNone(m)
            self.assertFalse(m.exact)  # 2-char summary not used for exact match

    def test_no_matching_workspace(self):
        with TemporaryDirectory() as d:
            home = Path(d)
            ws = str(home / "my-proj")
            _make_store(home, [("s1", ws, "o/my-proj", "main", "x", 100)])
            _make_lock(home, ws)
            m = self._resolve(home, "other-proj - Visual Studio Code", "")
            self.assertIsNone(m)

    def test_missing_db(self):
        with TemporaryDirectory() as d:
            m = self._resolve(Path(d), "my-proj - Visual Studio Code", "")
            self.assertIsNone(m)


if __name__ == "__main__":
    unittest.main()
