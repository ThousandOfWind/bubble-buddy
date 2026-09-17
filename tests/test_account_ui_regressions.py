"""Review regressions: auth epochs/unknown state and native layout/session routing.

Native methods are exercised with frame-recording AppKit stand-ins on Windows;
this is not a claim of a live macOS GUI test.
"""
import ast
import copy
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from bubble_buddy import config
from bubble_buddy.qt_overlay import VoiceDesktop


class AuthUiRegressionTest(unittest.TestCase):
    def desktop(self):
        return SimpleNamespace(
            _account_providers=lambda: ("copilot",), _account_text=lambda key, **kwargs: key,
            _refit_for_signin=Mock(), _check_auth_async=Mock(), _discard_worker=Mock(), _register_worker=Mock(),
            _signin_worker=None, _auth_worker=None, _auth_generation=0,
            signin_btn=Mock(), error=Mock(),
        )

    def test_close_waits_for_every_provider_even_after_public_worker_ref_is_cleared(self):
        from bubble_buddy.qt_overlay import SignInWorker
        for provider in ("azure", "codex", "copilot"):
            worker = SignInWorker(provider)
            desktop = SimpleNamespace(_active_workers={worker}, _signin_worker=None, error=Mock(),
                                     close=Mock(), setEnabled=Mock())
            event = Mock()
            with patch.object(worker, "isRunning", return_value=True), patch.object(worker, "requestInterruption") as interrupt, \
                    patch("bubble_buddy.qt_overlay.QTimer.singleShot"):
                VoiceDesktop.closeEvent(desktop, event)
                interrupt.assert_called_once()
                event.ignore.assert_called_once()
                event.accept.assert_not_called()
                self.assertIn(worker, desktop._closing_workers)
            with patch.object(worker, "isRunning", return_value=False):
                VoiceDesktop.closeEvent(desktop, event)
            event.accept.assert_called_once()

    def test_worker_reference_lasts_until_native_thread_finishes(self):
        worker = Mock()
        worker.isRunning.return_value = True
        desktop = SimpleNamespace(_active_workers={worker})
        VoiceDesktop._discard_worker(desktop, worker)
        self.assertIn(worker, desktop._active_workers)
        worker.isRunning.return_value = False
        VoiceDesktop._discard_worker(desktop, worker)
        self.assertNotIn(worker, desktop._active_workers)

    def test_retranslation_preserves_the_signin_action_state(self):
        desktop = self.desktop()
        with patch("bubble_buddy.qt_overlay.t", side_effect=lambda key: "translated:" + key):
            for state, expected in (("device", "translated:account.cancel"), ("opening", "translated:btn.signin_opening"),
                                    ("recover", "recover"), ("retry", "retry")):
                desktop._signin_label_state = state
                VoiceDesktop._retranslate_signin_label(desktop)
                desktop.signin_btn.setText.assert_called_with(expected)

    def test_superseded_results_remain_in_history_without_ui_or_delivery_side_effects(self):
        desktop = SimpleNamespace(_recording_generation=2, stream_worker=None, _recording_target=None,
                                 _discard_worker=Mock(), _add_history_entry=Mock(), transcript=Mock(), polished=Mock(),
                                 error=Mock(), _set_stage=Mock(), _paste_text=Mock())
        desktop._job_is_current = lambda worker: VoiceDesktop._job_is_current(desktop, worker)
        target = object()
        worker = SimpleNamespace(recording_generation=1, job_target=target, raw_text="old raw")
        VoiceDesktop._on_transcribed(desktop, "old raw", "old polished", worker)
        desktop._add_history_entry.assert_called_with("old raw", "old polished", target, note_key="msg.history_superseded")
        VoiceDesktop._on_failed(desktop, "old failure", worker)
        desktop._add_history_entry.assert_called_with("old raw", "", target, note_key="msg.history_failed", error="old failure")
        with patch("bubble_buddy.qt_overlay.PolishWorker") as polish:
            VoiceDesktop._on_stream_finished(desktop, "old realtime", worker)
            polish.assert_not_called()
        desktop.transcript.setPlainText.assert_not_called()
        desktop.polished.setPlainText.assert_not_called()
        desktop._set_stage.assert_not_called()
        desktop._paste_text.assert_not_called()
        desktop.error.setText.assert_not_called()

    def test_realtime_polish_failure_never_emits_successful_fallback(self):
        from bubble_buddy.qt_overlay import PolishWorker
        worker = PolishWorker("current raw", "copilot", None, False, "en", "copilot", "unused")
        results, errors = [], []
        worker.finished_text.connect(lambda *args: results.append(args))
        worker.failed.connect(errors.append)
        with patch("bubble_buddy.qt_overlay.polish_text", side_effect=RuntimeError("polish failed")):
            worker.run()
        self.assertEqual(results, [])
        self.assertEqual(errors, ["polish failed"])

    def test_old_asr_result_cannot_clear_new_recording_display(self):
        desktop = SimpleNamespace(_recording_generation=2, transcript=Mock(), polished=Mock(),
                                  _show_bubble=Mock(), polish="off")
        desktop._job_is_current = lambda worker: VoiceDesktop._job_is_current(desktop, worker)
        VoiceDesktop._on_raw_transcribed(desktop, "old", SimpleNamespace(recording_generation=1))
        desktop.transcript.setPlainText.assert_not_called()
        desktop.polished.clear.assert_not_called()
        VoiceDesktop._on_raw_transcribed(desktop, "new", SimpleNamespace(recording_generation=2))
        desktop.transcript.setPlainText.assert_called_once_with("new")

    def test_azure_refresh_timer_tracks_live_provider_changes(self):
        desktop = SimpleNamespace(_token_timer=None, _account_providers=lambda: ("copilot",), _refresh_azure_token=Mock())
        with patch("bubble_buddy.qt_overlay.QTimer") as factory:
            VoiceDesktop._sync_azure_refresh_timer(desktop)
            factory.assert_not_called()
            desktop._account_providers = lambda: ("azure",)
            timer = factory.return_value
            timer.isActive.return_value = False
            VoiceDesktop._sync_azure_refresh_timer(desktop)
            factory.assert_called_once_with(desktop)
            timer.setInterval.assert_called_once_with(20 * 60 * 1000)
            timer.start.assert_called_once()
            desktop._account_providers = lambda: ("copilot",)
            VoiceDesktop._sync_azure_refresh_timer(desktop)
            timer.stop.assert_called_once()
            desktop._account_providers = lambda: ("azure",)
            VoiceDesktop._sync_azure_refresh_timer(desktop)
            self.assertEqual(factory.call_count, 1)

    def test_new_raw_text_clears_previous_polished_text(self):
        desktop = SimpleNamespace(transcript=Mock(), polished=Mock(), _show_bubble=Mock(), polish="off")
        desktop._job_is_current = lambda worker: VoiceDesktop._job_is_current(desktop, worker)
        VoiceDesktop._on_raw_transcribed(desktop, "current raw")
        desktop.transcript.setPlainText.assert_called_once_with("current raw")
        desktop.polished.clear.assert_called_once()

    def test_device_handoff_shows_actual_expiry(self):
        desktop = self.desktop()
        VoiceDesktop._on_device_code(desktop, {"user_code": "TEST-CODE", "verification_uri": "https://github.com/login/device", "expires_in": 37})
        text = desktop.error.setText.call_args.args[0]
        self.assertIn("37", text)
        self.assertIn("TEST-CODE", text)
        self.assertNotIn("{expires}", text)

    def test_codex_desktop_limit_is_bounded_even_for_unlimited_config(self):
        from bubble_buddy import codex_client
        for requested in (0, 120, 600, -1, float("inf")):
            limit = config.recording_limit_seconds("codex", requested)
            self.assertEqual(limit, 119)
            self.assertLess(limit, codex_client._MAX_AUDIO_SECONDS)
            with patch.object(config, "load_config", return_value={"max_record_seconds": requested}):
                self.assertEqual(VoiceDesktop._max_record_seconds(SimpleNamespace(backend="codex")), limit)
        self.assertEqual(config.recording_limit_seconds("codex", 30), 30)
        self.assertEqual(config.recording_limit_seconds("azure", 0), 0)
        self.assertEqual(config.recording_limit_seconds("faster-whisper", 600), 600)

    def test_hotkey_polish_error_preserves_raw_without_automatic_paste(self):
        from bubble_buddy import cli
        with tempfile.TemporaryDirectory() as tmp:
            audio, destination = Path(tmp) / "capture.wav", Path(tmp) / "raw.txt"
            audio.write_bytes(b"placeholder")  # only testing post-ASR delivery here
            raw = {"plain_text": "current raw", "raw_text": "current raw"}
            session = SimpleNamespace(
                _current_audio_path=audio, _stop_streaming_audio=Mock(), streaming=False,
                _session_context_status=lambda: "", _report_status=Mock(),
                _transcribe_with_loaded_model=Mock(return_value=raw), polish="copilot", context_file=None,
                session_context=False, language_preference="en", polish_engine="copilot", ollama_model="unused",
                _target_app=None, plain=True, save_text=destination,
                copy_to_clipboard=True, paste_to_active_app=True, submit_to_active_app=True,
            )
            with patch.object(cli, "apply_polish_to_result", side_effect=RuntimeError("polish unavailable")), \
                    patch.object(cli, "copy_text") as copy_text, patch.object(cli, "paste_from_clipboard") as paste, \
                    redirect_stdout(io.StringIO()) as console, self.assertRaisesRegex(RuntimeError, "polish unavailable"):
                cli.HotkeySession._stop_and_process_recording(session)
            self.assertEqual(destination.read_text(encoding="utf-8"), "current raw\n")
            self.assertIn("current raw", console.getvalue())
            copy_text.assert_not_called()
            paste.assert_not_called()
            self.assertFalse(any(call.args[0].get("stage") == "done" for call in session._report_status.call_args_list))

    def test_pre_login_probe_cannot_reopen_banner_after_login_succeeds(self):
        desktop = self.desktop()
        old = {"providers": ("copilot",), "provider": "copilot", "signed_in": False, "auth_generation": 0}
        VoiceDesktop._on_signed_in(desktop, {"provider": "copilot", "signed_in": True})
        self.assertEqual(desktop._auth_generation, 1)
        desktop.signin_btn.hide.assert_called_once()
        desktop.signin_btn.reset_mock()
        desktop.error.reset_mock()
        VoiceDesktop._apply_auth_status(desktop, old)
        desktop.signin_btn.setVisible.assert_not_called()
        desktop.error.setText.assert_not_called()
        with patch("bubble_buddy.qt_overlay.QTimer.singleShot") as timer:
            VoiceDesktop._auth_status_finished(desktop, Mock(), 0)
            timer.assert_called_once_with(0, desktop._check_auth_async)

    def test_corrupt_credentials_offer_explicit_repair_without_claiming_logged_out(self):
        from bubble_buddy import account_auth
        from bubble_buddy.credential_store import load_credentials
        with self.assertRaises(RuntimeError) as caught:
            load_credentials(SimpleNamespace(load=lambda: "not-json"), "GitHub Copilot")
        client = SimpleNamespace(auth_status=Mock(side_effect=caught.exception))
        with patch.object(account_auth, "client", return_value=client):
            status = account_auth.auth_status(("copilot",))
        self.assertIsNone(status["signed_in"])
        self.assertTrue(status["reauth_recovery"])
        desktop = self.desktop()
        VoiceDesktop._apply_auth_status(desktop, {**status, "providers": ("copilot",)})
        desktop.signin_btn.setText.assert_called_with("recover")
        desktop.signin_btn.show.assert_called_once()
        desktop._check_auth_async.assert_not_called()
        self.assertIsNone(desktop._signin_worker)

    def test_probe_captures_generation_at_start_not_when_signal_arrives(self):
        desktop = self.desktop()
        desktop._auth_generation = 7
        desktop._apply_auth_status = Mock()
        desktop._auth_status_finished = Mock()
        with patch("bubble_buddy.qt_overlay.AuthStatusWorker") as constructor:
            worker = constructor.return_value
            VoiceDesktop._check_auth_async(desktop)
            callback = worker.ready.connect.call_args.args[0]
            desktop._auth_generation = 8
            callback({"signed_in": False, "providers": ("copilot",)})
        self.assertEqual(desktop._apply_auth_status.call_args.args[0]["auth_generation"], 7)

    def test_unknown_status_preserves_last_banner_and_provider(self):
        desktop = self.desktop()
        desktop._signin_provider = "copilot"
        for previous_state in (True, False):
            VoiceDesktop._apply_auth_status(desktop, {
                "providers": ("copilot",), "provider": "copilot", "signed_in": previous_state,
            })
            desktop.signin_btn.reset_mock()
            VoiceDesktop._apply_auth_status(desktop, {
                "providers": ("copilot",), "provider": "other", "signed_in": None, "error": "temporarily offline",
            })
            desktop.signin_btn.setVisible.assert_not_called()
            desktop.signin_btn.setText.assert_not_called()
            self.assertEqual(desktop._signin_provider, "copilot")
            desktop.error.setText.assert_called_with("temporarily offline")


def native_method(name, extra=None):
    """Load the actual method body without importing macOS-only frameworks."""
    path = Path(__file__).resolve().parents[1] / "src/bubble_buddy/overlay.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    controller = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SpriteOverlayController")
    method = next(node for node in controller.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {"__package__": "bubble_buddy", **(extra or {})}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class View:
    def __init__(self):
        self.frame = (0, 0, 0, 0)
        self.children = []
        self.parent = None
        self.content = None
    @classmethod
    def alloc(cls):
        return cls()
    @classmethod
    def labelWithString_(cls, text):
        node = cls()
        node.text = text
        return node
    def initWithFrame_(self, frame):
        self.frame = frame
        return self
    def initWithContentRect_styleMask_backing_defer_(self, frame, *args):
        self.frame = frame
        return self
    def contentView(self):
        if self.content is None:
            self.content = View()
        return self.content
    def addSubview_(self, view):
        view.parent = self
        self.children.append(view)
    def setFrame_(self, frame):
        self.frame = frame
    def setStringValue_(self, value):
        self.value = value
    def stringValue(self):
        return self.value
    def setDocumentView_(self, view):
        self.document = view
    def scrollPoint_(self, point):
        self.scroll_point = point
    def __getattr__(self, name):
        # Styling and presentation calls do not affect recorded frame geometry.
        return lambda *args: None


class Panel(View):
    pass


class Scroll(View):
    pass


class NativeAccountRegressionTest(unittest.TestCase):
    def test_auth_uses_live_cli_session_not_persisted_settings(self):
        settings = SimpleNamespace(load_config=Mock(side_effect=AssertionError("must use live session")))
        method = native_method("_account_providers", {"_config": settings})
        controller = SimpleNamespace(session=SimpleNamespace(backend="faster-whisper", polish_engine="copilot", polish="auto"))
        self.assertEqual(method(controller), ("copilot",))
        controller.session.polish = "off"
        self.assertEqual(method(controller), ())
        controller.session.backend = "azure"
        self.assertEqual(method(controller), ("azure",))
        settings.load_config.assert_not_called()

    def test_native_unknown_status_is_not_a_signed_out_claim(self):
        from bubble_buddy import account_auth
        controller = SimpleNamespace(_account_providers=lambda: ("copilot",), state=Mock(), _signin_thread=None)
        method = native_method("_safe_auth_status", {"t": lambda key, **kwargs: key})
        with patch.object(account_auth, "auth_status", return_value={"signed_in": None, "provider": "copilot", "error": "protected-store failure"}):
            method(controller)
        controller.state.update.assert_called_once_with({"error": "protected-store failure"})
        controller.state.reset_mock()
        with patch.object(account_auth, "auth_status", return_value={"signed_in": None, "provider": "copilot"}):
            method(controller)
        controller.state.update.assert_not_called()

    def test_native_probes_cannot_overwrite_active_device_handoff(self):
        from bubble_buddy import account_auth
        signin = SimpleNamespace(is_alive=lambda: True)
        threads = SimpleNamespace(Thread=Mock(), current_thread=lambda: object())
        controller = SimpleNamespace(_signin_thread=signin, _account_providers=lambda: ("copilot",),
                                     _safe_auth_status=Mock(), state=Mock())
        native_method("checkAzureStatus_", {"threading": threads})(controller, None)
        threads.Thread.assert_not_called()
        with patch.object(account_auth, "auth_status", return_value={"signed_in": False, "provider": "copilot"}):
            native_method("_safe_auth_status", {"threading": threads, "t": lambda key, **kw: key})(controller)
        controller.state.update.assert_not_called()

    def test_native_probe_started_before_login_is_ignored_after_thread_exits(self):
        from bubble_buddy import account_auth
        controller = SimpleNamespace(_auth_generation=2, _signin_thread=SimpleNamespace(is_alive=lambda: False),
                                     _account_providers=lambda: ("copilot",), state=Mock())
        with patch.object(account_auth, "auth_status", return_value={"signed_in": False, "provider": "copilot"}):
            native_method("_safe_auth_status", {"t": lambda key, **kw: key})(controller, 1, ("copilot",))
        controller.state.update.assert_not_called()

    def test_native_device_handoff_includes_expiry_and_capture_cap_uses_live_backend(self):
        from bubble_buddy import account_auth
        def login(provider, **kwargs):
            kwargs["on_code"]({"user_code": "TEST-CODE", "verification_uri": "https://github.com/login/device", "expires_in": 37})
            return {"signed_in": True}
        controller = SimpleNamespace(_account_providers=lambda: ("copilot",), state=Mock(), _safe_auth_status=Mock(),
                                     session=SimpleNamespace(backend="codex"))
        with patch.object(account_auth, "auth_status", return_value={"provider": "copilot", "signed_in": False}), \
                patch.object(account_auth, "sign_in", side_effect=login):
            native_method("_safe_sign_in", {"t": lambda key, **kwargs: (key, kwargs), "current_language": lambda: "en"})(controller)
        handoffs = [call.args[0]["error"] for call in controller.state.update.call_args_list]
        handoff = next(value for value in handoffs if value[0] == "account.device_code")
        self.assertEqual(handoff[1]["expires"], 37)
        with patch.object(config, "load_config", return_value={"max_record_seconds": 0}):
            self.assertEqual(native_method("_max_record_seconds", {"_config": config})(controller), 119)

    def test_account_control_visibility_follows_active_providers_and_collapsed_state(self):
        controller = SimpleNamespace(_account_button=Mock(), _collapsed=False, _account_providers=lambda: ())
        sync = native_method("_sync_account_button")
        sync(controller)
        controller._account_button.setHidden_.assert_called_with(True)
        controller._account_providers = lambda: ("copilot",)
        sync(controller)
        controller._account_button.setHidden_.assert_called_with(False)
        controller._collapsed = True
        sync(controller)
        controller._account_button.setHidden_.assert_called_with(True)

    def test_native_profile_fields_save_defaults_and_reject_invalid_values(self):
        settings = SimpleNamespace(DEFAULTS=config.DEFAULTS, save_config=Mock(return_value=Path("config.json")))
        save = native_method("saveSettings_", {"_config": settings, "t": lambda key, **kwargs: key})
        controller = SimpleNamespace(_settings_fields={}, _apply_settings=Mock(), state=Mock())
        values = {"copilot_reasoning_effort": "medium", "copilot_max_output_tokens": "4096"}
        def set_fields():
            controller._settings_fields = {key: SimpleNamespace(stringValue=lambda value=value: value) for key, value in values.items()}
        set_fields()
        save(controller, None)
        updates = settings.save_config.call_args.args[0]
        self.assertEqual(updates["copilot_reasoning_effort"], "medium")
        self.assertEqual(updates["copilot_max_output_tokens"], 4096)
        for effort, budget in (("ultra", "4096"), ("low", "0"), ("low", "16385"), ("low", "bad")):
            values.update(copilot_reasoning_effort=effort, copilot_max_output_tokens=budget)
            set_fields()
            settings.save_config.reset_mock()
            save(controller, None)
            settings.save_config.assert_not_called()
            self.assertEqual(controller.state.update.call_args.args[0]["stage"], "error")
        controller._settings_fields = {}
        save(controller, None)
        updates = settings.save_config.call_args.args[0]
        self.assertEqual(updates["copilot_reasoning_effort"], config.DEFAULTS["copilot_reasoning_effort"])
        self.assertEqual(updates["copilot_max_output_tokens"], config.DEFAULTS["copilot_max_output_tokens"])

    def test_settings_rows_scroll_without_overlapping_fixed_footer(self):
        settings = SimpleNamespace(load_config=lambda **kw: copy.deepcopy(config.DEFAULTS), DEFAULTS=config.DEFAULTS)
        method = native_method("_show_settings_window", {
            "_config": settings, "NSPanel": Panel, "NSScrollView": Scroll, "NSView": View,
            "NSButton": View, "NSTextField": View, "NSFont": Mock(), "NSColor": Mock(),
            "NSScreen": SimpleNamespace(mainScreen=lambda: SimpleNamespace(visibleFrame=lambda: SimpleNamespace(size=SimpleNamespace(height=600)))),
            "NSMakeRect": lambda *args: args, "NSMakePoint": lambda *args: args,
            "NSWindowStyleMaskTitled": 1, "NSWindowStyleMaskClosable": 2, "NSBackingStoreBuffered": 0,
            "_style": SimpleNamespace(TEXT="black", TEXT_MUTED="gray"), "_color": lambda color: color, "t": lambda key: key,
        })
        controller = SimpleNamespace(_settings_window=None)
        method(controller)
        panel = controller._settings_window
        self.assertLessEqual(panel.frame[3], 540)
        content = panel.contentView()
        scroll = next(child for child in content.children if isinstance(child, Scroll))
        form = scroll.document
        self.assertGreater(form.frame[3], scroll.frame[3])
        self.assertGreater(form.scroll_point[1], 0)
        self.assertIn("copilot_model", controller._settings_fields)
        self.assertIn("copilot_reasoning_effort", controller._settings_fields)
        self.assertIn("copilot_max_output_tokens", controller._settings_fields)
        self.assertIn("language", controller._settings_fields)
        for field in controller._settings_fields.values():
            self.assertIs(field.parent, form)
            self.assertGreaterEqual(field.frame[1], 0)
            self.assertLessEqual(field.frame[1] + field.frame[3], form.frame[3])
            self.assertLessEqual(field.frame[0] + field.frame[2], form.frame[2])
        for footer in (child for child in content.children if child is not scroll):
            self.assertLessEqual(footer.frame[1] + footer.frame[3], scroll.frame[1])
        # Hidden panels rebuild from config; visible panels keep unsaved drafts.
        settings.load_config = Mock(return_value={"copilot_model": "updated", "copilot_reasoning_effort": "high", "copilot_max_output_tokens": 4096})
        method(controller)
        self.assertIsNot(controller._settings_window, panel)
        self.assertEqual(controller._settings_fields["copilot_model"].stringValue(), "updated")
        self.assertEqual(controller._settings_fields["copilot_max_output_tokens"].stringValue(), "4096")
        controller._settings_window.isVisible = lambda: True
        controller._settings_fields["copilot_model"].setStringValue_("unsaved")
        settings.load_config.reset_mock()
        method(controller)
        settings.load_config.assert_not_called()
        self.assertEqual(controller._settings_fields["copilot_model"].stringValue(), "unsaved")


if __name__ == "__main__":
    unittest.main()
