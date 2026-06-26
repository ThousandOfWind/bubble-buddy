from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

import objc
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSMakeRect,
    NSPanel,
    NSScrollView,
    NSTextField,
    NSTextView,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSTimer
from pynput import keyboard

from .cli import AppTarget, DEFAULT_HOTKEY, HotkeySession, get_frontmost_app_info, normalize_hotkey


class OverlayState:
    def __init__(self, hotkey: str) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, object] = {
            "stage": "idle",
            "hotkey": hotkey,
            "plain_text": "",
            "audio_path": "",
            "error": "If the hotkey does not respond, click Start Recording and ensure Input Monitoring is enabled for your terminal or VS Code.",
            "copied": False,
            "pasted": False,
            "submitted": False,
            "target_app": "",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def update(self, patch: dict[str, object]) -> None:
        with self._lock:
            self._state.update(patch)
            self._state["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return dict(self._state)


class SpriteOrbView(NSView):
    def initWithFrame_(self, frame):  # type: ignore[override]
        self = objc.super(SpriteOrbView, self).initWithFrame_(frame)
        if self is None:
            return None

        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(80.0)
        self.layer().setMasksToBounds_(True)

        self.face_label = NSTextField.labelWithString_("•ᴗ•")
        self.face_label.setFrame_(NSMakeRect(25, 38, 110, 60))
        self.face_label.setAlignment_(1)
        self.face_label.setFont_(NSFont.systemFontOfSize_weight_(34, 0.6))
        self.face_label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(0.05, 1.0))
        self.addSubview_(self.face_label)

        self.set_stage("idle")
        return self

    def set_stage(self, stage: str) -> None:
        colors = {
            "idle": NSColor.colorWithCalibratedRed_green_blue_alpha_(0.43, 0.66, 0.99, 1.0),
            "recording": NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.36, 0.45, 1.0),
            "transcribing": NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.82, 0.40, 1.0),
            "transcribed": NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.82, 0.40, 1.0),
            "done": NSColor.colorWithCalibratedRed_green_blue_alpha_(0.34, 0.80, 0.60, 1.0),
            "error": NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.42, 0.42, 1.0),
        }
        faces = {
            "idle": "•ᴗ•",
            "recording": "●ᴗ●",
            "transcribing": "•…•",
            "transcribed": "•…•",
            "done": "•‿•",
            "error": "•︵•",
        }
        self.layer().setBackgroundColor_(colors.get(stage, colors["idle"]).CGColor())
        self.face_label.setStringValue_(faces.get(stage, "•ᴗ•"))


class SpriteOverlayController(NSObject):
    def initWithState_session_listener_(self, state: OverlayState, session: HotkeySession, listener) -> "SpriteOverlayController":
        self = objc.super(SpriteOverlayController, self).init()
        if self is None:
            return None

        self.state = state
        self.session = session
        self.listener = listener
        self.window = None
        self.sprite = None
        self.status_label = None
        self.tip_label = None
        self.transcript_view = None
        self.error_label = None
        self._preferred_target: AppTarget | None = None
        return self

    def build_window(self) -> None:
        width = 380
        height = 470
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskFullSizeContentView
            | NSWindowStyleMaskResizable
        )
        window = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(60, 780, width, height),
            style,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("Copilot Voice Sprite")
        window.setReleasedWhenClosed_(False)
        window.setLevel_(NSFloatingWindowLevel)
        window.setMovableByWindowBackground_(True)
        window.setTitleVisibility_(1)
        window.setTitlebarAppearsTransparent_(True)
        window.setDelegate_(self)
        window.setFloatingPanel_(True)
        window.setBecomesKeyOnlyIfNeeded_(False)
        window.setHidesOnDeactivate_(False)
        window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        window.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.04, 0.07, 0.13, 0.96))
        self.window = window

        content = window.contentView()

        sprite = SpriteOrbView.alloc().initWithFrame_(NSMakeRect(110, 260, 160, 160))
        content.addSubview_(sprite)
        self.sprite = sprite

        status = NSTextField.labelWithString_("IDLE")
        status.setFrame_(NSMakeRect(40, 225, 300, 28))
        status.setAlignment_(1)
        status.setFont_(NSFont.systemFontOfSize_weight_(18, 0.65))
        status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(0.92, 1.0))
        content.addSubview_(status)
        self.status_label = status

        hotkey_text = self.state.snapshot()["hotkey"]
        tip = NSTextField.labelWithString_(f"Hotkey: {hotkey_text}")
        tip.setFrame_(NSMakeRect(40, 200, 300, 22))
        tip.setAlignment_(1)
        tip.setFont_(NSFont.systemFontOfSize_(12))
        tip.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.69, 0.88, 1.0))
        content.addSubview_(tip)
        self.tip_label = tip

        start_button = NSButton.alloc().initWithFrame_(NSMakeRect(58, 164, 120, 28))
        start_button.setTitle_("Start Recording")
        start_button.setBezelStyle_(1)
        start_button.setTarget_(self)
        start_button.setAction_("startRecording:")
        content.addSubview_(start_button)

        stop_button = NSButton.alloc().initWithFrame_(NSMakeRect(202, 164, 120, 28))
        stop_button.setTitle_("Stop Recording")
        stop_button.setBezelStyle_(1)
        stop_button.setTarget_(self)
        stop_button.setAction_("stopRecording:")
        content.addSubview_(stop_button)

        quit_button = NSButton.alloc().initWithFrame_(NSMakeRect(140, 132, 100, 24))
        quit_button.setTitle_("Quit")
        quit_button.setBezelStyle_(1)
        quit_button.setTarget_(self)
        quit_button.setAction_("quitOverlay:")
        content.addSubview_(quit_button)

        transcript_title = NSTextField.labelWithString_("Transcript")
        transcript_title.setFrame_(NSMakeRect(28, 108, 120, 18))
        transcript_title.setFont_(NSFont.systemFontOfSize_weight_(12, 0.6))
        transcript_title.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.69, 0.88, 1.0))
        content.addSubview_(transcript_title)

        transcript_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(28, 48, 324, 58))
        transcript_scroll.setBorderType_(0)
        transcript_scroll.setHasVerticalScroller_(True)
        transcript_scroll.setDrawsBackground_(False)

        transcript_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 324, 58))
        transcript_view.setEditable_(False)
        transcript_view.setSelectable_(True)
        transcript_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 0.4))
        transcript_view.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.03, 0.05, 0.11, 0.9))
        transcript_view.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(0.93, 1.0))
        transcript_view.setString_("Waiting for speech…")
        transcript_scroll.setDocumentView_(transcript_view)
        content.addSubview_(transcript_scroll)
        self.transcript_view = transcript_view

        error_title = NSTextField.labelWithString_("Status / Error")
        error_title.setFrame_(NSMakeRect(28, 24, 140, 18))
        error_title.setFont_(NSFont.systemFontOfSize_weight_(12, 0.6))
        error_title.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.69, 0.88, 1.0))
        content.addSubview_(error_title)

        error_label = NSTextField.labelWithString_(str(self.state.snapshot()["error"]))
        error_label.setFrame_(NSMakeRect(28, 4, 324, 18))
        error_label.setFont_(NSFont.systemFontOfSize_(12))
        error_label.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.74, 0.77, 1.0))
        error_label.setLineBreakMode_(2)
        error_label.setAllowsDefaultTighteningForTruncation_(True)
        content.addSubview_(error_label)
        self.error_label = error_label

    def show(self) -> None:
        assert self.window is not None
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.25,
            self,
            "refreshState:",
            None,
            True,
        )

    def refreshState_(self, _timer) -> None:
        snapshot = self.state.snapshot()
        stage = str(snapshot.get("stage", "idle"))
        plain_text = str(snapshot.get("plain_text", "") or "Waiting for speech…")
        error = str(snapshot.get("error", "") or "No errors.")
        target_app = str(snapshot.get("target_app", "")).strip()
        self._update_preferred_target()

        assert self.sprite is not None
        assert self.status_label is not None
        assert self.transcript_view is not None
        assert self.error_label is not None

        self.sprite.set_stage(stage)
        status_text = stage.upper()
        if stage == "done" and target_app:
            status_text = f"DONE -> {target_app}"
        self.status_label.setStringValue_(status_text)
        self.transcript_view.setString_(plain_text)
        self.error_label.setStringValue_(error)

    def windowWillClose_(self, _notification) -> None:
        self.listener.stop()
        self.session.stop_if_recording()
        NSApp.stop_(None)

    def windowShouldClose_(self, _sender) -> bool:
        self.quitOverlay_(None)
        return True

    def startRecording_(self, _sender) -> None:
        self.state.update({"stage": "recording", "error": "Start button clicked."})
        threading.Thread(target=self._safe_start_recording, daemon=True).start()

    def stopRecording_(self, _sender) -> None:
        self.state.update({"stage": "transcribing", "error": "Stop button clicked."})
        threading.Thread(target=self._safe_stop_recording, daemon=True).start()

    def quitOverlay_(self, _sender) -> None:
        self.state.update({"error": "Closing overlay."})
        self.listener.stop()
        self.session.stop_if_recording()
        if self.window is not None:
            self.window.orderOut_(None)
        NSApp.terminate_(None)

    def _safe_start_recording(self) -> None:
        try:
            self.session.start_recording()
        except Exception as exc:  # noqa: BLE001
            self.state.update({"stage": "error", "error": f"Start failed: {exc}"})

    def _safe_stop_recording(self) -> None:
        try:
            self.session.stop_recording()
        except Exception as exc:  # noqa: BLE001
            self.state.update({"stage": "error", "error": f"Stop failed: {exc}"})

    def get_preferred_target(self) -> AppTarget | None:
        return self._preferred_target

    def _update_preferred_target(self) -> None:
        try:
            frontmost = get_frontmost_app_info()
        except Exception:
            return
        if frontmost.pid != os.getpid():
            self._preferred_target = frontmost


def run_overlay(
    *,
    hotkey: str = DEFAULT_HOTKEY,
    language: str,
    model_name: str,
    copy_to_clipboard: bool,
    paste_to_active_app: bool,
    submit_to_active_app: bool,
    plain: bool,
    save_text: Path | None,
    hf_endpoint: str,
    replacement_pairs: list[str],
    replacements_file: Path | None,
) -> None:
    state = OverlayState(hotkey)
    should_copy = copy_to_clipboard or not (paste_to_active_app or submit_to_active_app)
    session = HotkeySession(
        language=language,
        model_name=model_name,
        copy_to_clipboard=should_copy,
        paste_to_active_app=paste_to_active_app or submit_to_active_app,
        submit_to_active_app=submit_to_active_app,
        plain=plain,
        save_text=save_text,
        hf_endpoint=hf_endpoint,
        replacement_pairs=replacement_pairs,
        replacements_file=replacements_file,
        status_reporter=state.update,
    )
    listener = keyboard.GlobalHotKeys({normalize_hotkey(hotkey): session.toggle_recording})
    listener.start()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    controller = SpriteOverlayController.alloc().initWithState_session_listener_(state, session, listener)
    session.target_app_getter = controller.get_preferred_target
    controller.build_window()
    controller.show()

    print(f"Overlay is running. Press {hotkey} to start/stop recording.", flush=True)
    try:
        app.run()
    finally:
        listener.stop()
        session.stop_if_recording()
