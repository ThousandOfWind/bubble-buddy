"""Mac frontend performance seams, without recording or cloud calls.

Controller methods are executed from the actual source with AppKit stand-ins.
A macOS CI import smoke test separately checks real objc/AppKit imports. Neither
proves microphone permissions, MLX runtime performance, or fullscreen behavior.
"""
import ast
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from module_stubs import stub_modules

from bubble_buddy import copilot_client
from bubble_buddy.frontend_bubble import BubbleKind, make_bubble


def native_method(name, **extra):
    path = Path(__file__).resolve().parents[1] / "src/bubble_buddy/overlay.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    controller = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SpriteOverlayController")
    method = next(node for node in controller.body if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), method], type_ignores=[])
    namespace = {"__name__": "bubble_buddy.overlay", "__package__": "bubble_buddy",
                 "BubbleKind": BubbleKind, "make_bubble": make_bubble,
                 "t": lambda key, **kwargs: key, **extra}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


class BackgroundWarmupTest(unittest.TestCase):
    def test_single_flight_cancellation_and_close_prevent_later_work(self):
        entered = threading.Event()
        cancellation_seen = threading.Event()
        def prepare(*, cancelled):
            entered.set()
            while not cancelled():
                cancellation_seen.wait(0.01)
            cancellation_seen.set()
            return False
        preparation = copilot_client.BackgroundWarmup()
        with patch.object(copilot_client, "warmup", side_effect=prepare) as warmup:
            try:
                preparation.start(enabled=False)
                warmup.assert_not_called()
                preparation.start(enabled=True)
                self.assertTrue(entered.wait(2))
                preparation.start(enabled=True)
                self.assertEqual(warmup.call_count, 1)
                preparation.start(enabled=False)
                self.assertTrue(cancellation_seen.wait(2))
            finally:
                preparation.close(wait=True)
            preparation.start(enabled=True)
            self.assertEqual(warmup.call_count, 1)

    def test_normal_exit_wait_is_bounded_even_if_network_worker_is_stuck(self):
        preparation = copilot_client.BackgroundWarmup()
        worker = Mock()
        preparation._thread = worker
        preparation.close()
        worker.join.assert_not_called()
        preparation.close(wait=True)
        worker.join.assert_called_once_with(timeout=2.0)
        self.assertTrue(preparation._cancel.is_set())

    def test_optional_preparation_failure_stays_off_the_ui_path(self):
        preparation = copilot_client.BackgroundWarmup()
        with patch.object(copilot_client, "warmup", side_effect=RuntimeError("offline")) as warmup:
            preparation.start(enabled=True)
            preparation.close(wait=True)
            warmup.assert_called_once()


class ImportIsolationTest(unittest.TestCase):
    def test_module_stub_preserves_other_imports_and_restores_only_its_slots(self):
        replaced, imported = "_bb_replaced_test_module", "_bb_imported_test_module"
        original, temporary, newly_imported = object(), object(), object()
        sys.modules[replaced] = original
        try:
            with stub_modules({replaced: temporary}):
                self.assertIs(sys.modules[replaced], temporary)
                sys.modules[imported] = newly_imported
            self.assertIs(sys.modules[replaced], original)
            self.assertIs(sys.modules[imported], newly_imported)
        finally:
            sys.modules.pop(replaced, None)
            sys.modules.pop(imported, None)


class NativePerformanceTest(unittest.TestCase):
    def test_live_session_enables_warmup_for_mlx_and_whisper_not_disabled_polish(self):
        method = native_method("_warmup_copilot")
        for backend in ("mlx", "faster-whisper", "azure", "codex"):
            controller = SimpleNamespace(session=SimpleNamespace(backend=backend, polish_engine="copilot", polish="auto"),
                                         _copilot_preparation=Mock())
            method(controller)
            controller._copilot_preparation.start.assert_called_with(enabled=True)
            controller.session.polish = "off"
            method(controller)
            controller._copilot_preparation.start.assert_called_with(enabled=False)
            controller.session.polish, controller.session.polish_engine = "auto", "rules"
            method(controller)
            controller._copilot_preparation.start.assert_called_with(enabled=False)

    def test_capture_boundaries_prepare_without_calling_appkit_from_worker(self):
        for name, action in (("_safe_start_recording", "start_recording"), ("_safe_stop_recording", "stop_recording")):
            calls = []
            controller = SimpleNamespace(
                _warmup_copilot=lambda: calls.append("prepare"), session=Mock(),
                performSelectorOnMainThread_withObject_waitUntilDone_=Mock(), state=Mock(), _recording_transition=True,
            )
            getattr(controller.session, action).side_effect = lambda: calls.append("capture")
            native_method(name)(controller)
            self.assertEqual(calls, ["prepare", "capture"])
            self.assertFalse(controller._recording_transition)
            controller.performSelectorOnMainThread_withObject_waitUntilDone_.assert_called_once()
            controller.state.update.assert_not_called()

    def test_collapsed_native_overlay_shows_raw_while_polish_is_pending(self):
        controller = SimpleNamespace(_collapsed=True, _last_seen_stage="transcribing", _show_bubble=Mock(),
                                     _hide_bubble_key=Mock(), _last_bubble_signature="")
        native_method("_maybe_show_stage_bubble")(controller, "transcribed", {
            "plain_text": "raw ASR before cloud polish", "raw_text": "raw ASR before cloud polish",
            "rephrased_text": "", "audio_path": "fixture.wav",
        })
        bubble = controller._show_bubble.call_args.args[0]
        self.assertEqual(bubble.text, "raw ASR before cloud polish")
        self.assertEqual(bubble.kind, BubbleKind.SPEECH)

    def test_native_quit_cancels_without_waiting_on_network_on_appkit_thread(self):
        app = Mock()
        controller = SimpleNamespace(_copilot_preparation=Mock(), listener=Mock(), state=Mock(),
                                     _stop_max_record_timer=Mock(), _stop_session_quietly=Mock(), window=Mock())
        native_method("quitOverlay_", NSApp=app)(controller, None)
        controller._copilot_preparation.close.assert_called_once_with()
        app.terminate_.assert_called_once_with(None)

    def test_normal_event_loop_exit_has_a_warmup_drain(self):
        path = Path(__file__).resolve().parents[1] / "src/bubble_buddy/overlay.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        runner = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_overlay")
        text = ast.unparse(runner)
        self.assertIn("controller._copilot_preparation.close(wait=True)", text)
        # Do not inadvertently advertise the Qt rolling decoder on the native MLX path.
        self.assertNotIn("PreviewDecoder", text)
        self.assertNotIn("local_preview", text)


if __name__ == "__main__":
    unittest.main()
