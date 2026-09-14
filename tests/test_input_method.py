import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from bubble_buddy.qt_overlay import VoiceDesktop

_app = QApplication.instance() or QApplication([])


class _FakeService:
    def __init__(self):
        self.typed = []
        self.pasted = []

    def restore_focus(self, _target):
        pass

    def type_text(self, text, *, submit=False):
        self.typed.append((text, submit))

    def paste_keystroke(self, *, submit=False):
        self.pasted.append(submit)

    def set_launch_at_startup(self, _enabled):
        return True


def _make_widget(input_method):
    w = VoiceDesktop(
        hotkey="f9",
        language="zh",
        model_name="small",
        backend="faster-whisper",
        mlx_model="",
        paste_to_active_app=True,
        submit_to_active_app=False,
        copy_to_clipboard=False,
        hf_endpoint="",
        replacement_pairs=[],
        replacements_file=None,
        polish="off",
        context_file=None,
        session_context=False,
        language_preference="zh-en",
        polish_engine="rules",
        ollama_model="q",
    )
    w.input_method = input_method
    return w


class InputMethodTest(unittest.TestCase):
    """The output/delivery input_method chooses direct typing vs clipboard paste."""

    def setUp(self):
        import bubble_buddy.qt_overlay as qo

        self._orig = qo.get_platform_services
        self.svc = _FakeService()
        qo.get_platform_services = lambda: self.svc

    def tearDown(self):
        import bubble_buddy.qt_overlay as qo

        qo.get_platform_services = self._orig
        self.w.close()

    def test_type_method_types_directly(self):
        self.w = _make_widget("type")
        self.w.enforce_topmost = lambda: None
        self.w._paste_text("你好 hello", target=None)
        self.assertEqual(self.svc.typed, [("你好 hello", False)])
        self.assertEqual(self.svc.pasted, [])

    def test_paste_method_uses_clipboard(self):
        self.w = _make_widget("paste")
        self.w.enforce_topmost = lambda: None
        self.w._paste_text("你好 hello", target=None)
        self.assertEqual(self.svc.pasted, [False])
        self.assertEqual(self.svc.typed, [])

    def test_default_config_input_method_is_paste(self):
        from bubble_buddy import config

        self.w = _make_widget("paste")
        self.assertEqual(config.DEFAULTS["input_method"], "paste")


if __name__ == "__main__":
    unittest.main()
