"""Qt scheduling for optional metadata-only Copilot preparation."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from bubble_buddy import copilot_client
from bubble_buddy.qt_overlay import CopilotWarmupWorker, VoiceDesktop


class CopilotWarmupUiTest(unittest.TestCase):
    def desktop(self):
        return SimpleNamespace(polish="auto", polish_engine="copilot", _closing=False,
                               _account_providers=lambda: ("copilot",), _register_worker=Mock())

    def test_only_one_worker_runs_and_disabling_polish_cancels_it(self):
        desktop = self.desktop()
        with patch("bubble_buddy.qt_overlay.CopilotWarmupWorker") as factory:
            worker = factory.return_value
            worker.isRunning.return_value = True
            VoiceDesktop._warmup_copilot(desktop)
            VoiceDesktop._warmup_copilot(desktop)
            factory.assert_called_once()
            worker.start.assert_called_once()
            desktop._register_worker.assert_called_once_with(worker)
            desktop.polish = "off"
            VoiceDesktop._warmup_copilot(desktop)
            worker.requestInterruption.assert_called_once()

    def test_off_other_engine_and_closing_never_prepare_copilot(self):
        for updates in ({"polish": "off"}, {"polish_engine": "rules"}, {"_closing": True},
                        {"_account_providers": lambda: ()}):
            desktop = self.desktop()
            for key, value in updates.items():
                setattr(desktop, key, value)
            with patch("bubble_buddy.qt_overlay.CopilotWarmupWorker") as factory:
                VoiceDesktop._warmup_copilot(desktop)
            factory.assert_not_called()

    def test_preparation_failure_is_optional_and_never_starts_inference(self):
        worker = CopilotWarmupWorker()
        with patch.object(copilot_client, "warmup", side_effect=copilot_client.AuthRequiredError()) as prepare, \
                patch.object(copilot_client, "polish") as inference:
            worker.run()
        self.assertTrue(callable(prepare.call_args.kwargs["cancelled"]))
        inference.assert_not_called()

    def test_close_drains_preparation_thread(self):
        worker = CopilotWarmupWorker()
        desktop = SimpleNamespace(_active_workers={worker}, _copilot_warmup_worker=worker,
                                  error=Mock(), close=Mock(), setEnabled=Mock())
        event = Mock()
        with patch.object(worker, "isRunning", return_value=True), \
                patch.object(worker, "requestInterruption") as cancel, \
                patch("bubble_buddy.qt_overlay.QTimer.singleShot"):
            VoiceDesktop.closeEvent(desktop, event)
        cancel.assert_called_once()
        self.assertIn(worker, desktop._closing_workers)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()
        with patch.object(worker, "isRunning", return_value=False):
            VoiceDesktop.closeEvent(desktop, event)
        event.accept.assert_called_once()


if __name__ == "__main__":
    unittest.main()
