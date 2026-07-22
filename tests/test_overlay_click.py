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

    def _press(self):
        self.w.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton))

    def _release(self):
        self.w.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton))

    def _dblclick(self):
        self.w.mouseDoubleClickEvent(_mouse(QEvent.Type.MouseButtonDblClick, Qt.MouseButton.LeftButton))

    def test_single_click_expands(self):
        self._press()
        self._release()
        # The expand is deferred until the double-click timer fires.
        self.assertEqual(self.exp, 0)
        self.assertTrue(self.w._collapsed_click_timer.isActive())
        self.w._collapsed_click_timer.timeout.emit()
        self.assertEqual(self.exp, 1)
        self.assertEqual(self.rec, 0)

    def test_double_click_records_and_does_not_expand(self):
        # Qt delivers press, release, double-click, release for a double-click.
        self._press()
        self._release()
        self._dblclick()
        self._release()
        self.assertEqual(self.rec, 1)
        self.assertEqual(self.exp, 0)
        self.assertFalse(self.w._collapsed_click_timer.isActive())

    def test_double_click_ignored_when_expanded(self):
        self.w._collapsed = False
        self._dblclick()
        self.assertEqual(self.rec, 0)


if __name__ == "__main__":
    unittest.main()
