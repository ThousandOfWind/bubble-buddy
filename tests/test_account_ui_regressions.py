"""Review regressions: auth epochs/unknown state and native layout/session routing.

Native methods are exercised with frame-recording AppKit stand-ins on Windows;
this is not a claim of a live macOS GUI test.
"""
import ast
import copy
import os
import unittest
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
            _refit_for_signin=Mock(), _check_auth_async=Mock(), _discard_worker=Mock(),
            _signin_worker=None, _auth_worker=None, _auth_generation=0,
            signin_btn=Mock(), error=Mock(),
        )

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


if __name__ == "__main__":
    unittest.main()
