import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

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


def _mouse(kind, button):
    return QMouseEvent(
        kind,
        QPointF(10, 10),
        QPointF(50, 50),
        button,
        button if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


class CollapsedClickTest(unittest.TestCase):
    """Collapsed-pet interaction: left-click toggles recording (high frequency),
    right-click expands the panel (low frequency)."""

    def setUp(self):
        self.w = _make_widget()
        self.w._collapse()
        self.w._collapsed = True
        self.rec = 0
        self.exp = 0
        self.w.toggle_recording = lambda: setattr(self, "rec", self.rec + 1)
        self.w._expand = lambda: setattr(self, "exp", self.exp + 1)

    def tearDown(self):
        self.w.close()

    def _press(self, button=Qt.MouseButton.LeftButton):
        self.w.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, button))

    def _release(self, button=Qt.MouseButton.LeftButton):
        self.w.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, button))

    def test_left_click_toggles_recording(self):
        self._press()
        self._release()
        self.assertEqual(self.rec, 1)
        self.assertEqual(self.exp, 0)

    def test_left_drag_does_not_toggle_recording(self):
        self._press()
        self.w._moved = True  # simulate a drag
        self._release()
        self.assertEqual(self.rec, 0)
        self.assertEqual(self.exp, 0)

    def test_right_click_expands(self):
        self._press(Qt.MouseButton.RightButton)
        self.assertEqual(self.exp, 1)
        self.assertEqual(self.rec, 0)

    def test_left_click_ignored_when_expanded(self):
        self.w._collapsed = False
        self._press()
        self._release()
        self.assertEqual(self.rec, 0)

    def test_right_click_ignored_when_expanded(self):
        self.w._collapsed = False
        self._press(Qt.MouseButton.RightButton)
        self.assertEqual(self.exp, 0)


if __name__ == "__main__":
    unittest.main()
