import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from bubble_buddy import config, i18n
from bubble_buddy.qt_overlay import VoiceDesktop

_app = QApplication.instance() or QApplication([])


def _make_widget():
    return VoiceDesktop(
        hotkey="f9",
        language="zh",
        model_name="small",
        backend="faster-whisper",
        mlx_model="",
        paste_to_active_app=False,
        submit_to_active_app=False,
        copy_to_clipboard=False,
        hf_endpoint="https://hf-mirror.com",
        replacement_pairs=[],
        replacements_file=None,
        polish="off",
        context_file=None,
        session_context=False,
        language_preference="zh-en",
        polish_engine="rules",
        ollama_model="qwen3:latest",
    )


class GreetingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        self._tmp.write('{"backend": "faster-whisper", "polish_engine": "rules"}')
        self._tmp.close()
        self._prev_env = os.environ.get("BUBBLE_BUDDY_CONFIG")
        os.environ["BUBBLE_BUDDY_CONFIG"] = self._tmp.name
        config.load_config(reload=True)
        i18n.set_language("zh")

    def tearDown(self):
        if self._prev_env is None:
            os.environ.pop("BUBBLE_BUDDY_CONFIG", None)
        else:
            os.environ["BUBBLE_BUDDY_CONFIG"] = self._prev_env
        config.load_config(reload=True)
        i18n.set_language("zh")
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def test_greeting_shows_once_and_persists_flag(self):
        w = _make_widget()
        w._collapse()
        calls = []
        w._show_greeting = lambda: calls.append(1)  # type: ignore[method-assign]

        self.assertFalse(config.load_config().get("first_launch_done"))
        w._maybe_show_greeting()
        self.assertEqual(len(calls), 1)
        self.assertTrue(config.load_config().get("first_launch_done"))

        # A second call must not greet again (flag is now set).
        w._maybe_show_greeting()
        self.assertEqual(len(calls), 1)

    def test_greeting_text_contains_hotkey(self):
        w = _make_widget()
        w._collapse()
        w._maybe_show_greeting()
        self.assertTrue(w._bubble.isVisible())
        self.assertIn("F9", w._bubble._text)
        self.assertIn("BB", w._bubble._text)


if __name__ == "__main__":
    unittest.main()
