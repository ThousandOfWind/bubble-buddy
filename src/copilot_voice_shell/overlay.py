from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import objc
import pyperclip
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAnimationContext,
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSBezierPath,
    NSEvent,
    NSFont,
    NSImage,
    NSMakePoint,
    NSMakeRect,
    NSPanel,
    NSScreenSaverWindowLevel,
    NSScreen,
    NSScrollView,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSWorkspace,
    NSTextField,
    NSTextView,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSTimer, NSString
from pynput import keyboard

from . import config as _config
from .cli import AppTarget, DEFAULT_HOTKEY, HotkeySession, get_frontmost_app_info, normalize_hotkey
from .frontend_contract import FrontendState
from . import frontend_style as _style
from .frontend_bubble import BubbleKind, BubbleSpec, make_bubble
from .i18n import set_language, t


def resolve_delivery_flags(
    cfg: dict,
    copy_to_clipboard: bool | None,
    paste_to_active_app: bool | None,
    submit_to_active_app: bool | None,
) -> tuple[bool, bool, bool]:
    def _resolve(flag: bool | None, key: str) -> bool:
        return bool(flag) if flag is not None else bool(cfg.get(key))

    resolved_copy = _resolve(copy_to_clipboard, "copy_to_clipboard")
    resolved_paste = _resolve(paste_to_active_app, "paste_to_active_app")
    resolved_submit = _resolve(submit_to_active_app, "submit_to_active_app")
    should_copy = resolved_copy or not (resolved_paste or resolved_submit)
    return should_copy, resolved_paste or resolved_submit, resolved_submit


def _color(hex_color: str, alpha: float = 1.0) -> NSColor:
    value = hex_color.lstrip("#")
    r = int(value[0:2], 16) / 255.0
    g = int(value[2:4], 16) / 255.0
    b = int(value[4:6], 16) / 255.0
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)


def _style_icon_button(button: NSButton, title: str, tooltip: str = "", symbol: str = "") -> None:
    button.setTitle_("" if symbol else title)
    button.setToolTip_(tooltip or title)
    button.setBezelStyle_(1)
    button.setBordered_(False)
    button.setWantsLayer_(True)
    layer = button.layer()
    layer.setBackgroundColor_(_color(_style.BUTTON_BG).CGColor())
    layer.setCornerRadius_(_style.ICON_BUTTON_RADIUS)
    layer.setBorderWidth_(1.0)
    layer.setBorderColor_(_color(_style.BUTTON_BORDER).CGColor())
    button.setFont_(NSFont.systemFontOfSize_weight_(18, 0.55))
    button.setContentTintColor_(_color(_style.TEXT))
    if symbol:
        try:
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, tooltip or title)
            if image is not None:
                button.setImage_(image)
        except Exception:  # noqa: BLE001
            button.setTitle_(title)


def _make_hotkey_listener(hotkey: str, session: HotkeySession):
    def _on_hotkey() -> None:
        threading.Thread(target=session.toggle_recording, daemon=True).start()

    return keyboard.GlobalHotKeys({normalize_hotkey(hotkey): _on_hotkey})


class OverlayState:
    def __init__(self, hotkey: str) -> None:
        self._lock = threading.Lock()
        self._state = FrontendState(
            hotkey=hotkey,
            error=t("msg.hotkey_help"),
        )
        self._updated_at = datetime.now().isoformat(timespec="seconds")

    def update(self, patch: dict[str, object]) -> None:
        with self._lock:
            self._state.apply(patch)
            self._updated_at = datetime.now().isoformat(timespec="seconds")

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            snap = self._state.snapshot()
            snap["updated_at"] = self._updated_at
            return snap


class SpriteOrbView(NSView):
    def initWithFrame_(self, frame):  # type: ignore[override]
        self = objc.super(SpriteOrbView, self).initWithFrame_(frame)
        if self is None:
            return None

        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(80.0)
        self.layer().setMasksToBounds_(False)
        self.layer().setShadowOpacity_(0.42)
        self.layer().setShadowRadius_(22.0)
        self.layer().setShadowOffset_((0.0, -2.0))

        self.face_label = NSTextField.labelWithString_("•ᴗ•")
        self.face_label.setFrame_(NSMakeRect(22, 35, 116, 66))
        self.face_label.setAlignment_(1)
        self.face_label.setFont_(NSFont.systemFontOfSize_weight_(38, 0.62))
        self.face_label.setTextColor_(_color(_style.ORB_INK))
        self.addSubview_(self.face_label)
        self.click_handler = None
        self._drag_start = None
        self._window_start = None
        self._did_drag = False
        self._stage = "idle"
        self._t0 = time.perf_counter()

        self.set_stage("idle")
        return self

    def mouseDown_(self, event) -> None:
        self._drag_start = NSEvent.mouseLocation()
        window = self.window()
        self._window_start = window.frame().origin if window is not None else None
        self._did_drag = False

    def mouseDragged_(self, event) -> None:
        if self._drag_start is None or self._window_start is None:
            return
        current = NSEvent.mouseLocation()
        dx = current.x - self._drag_start.x
        dy = current.y - self._drag_start.y
        if abs(dx) > 4 or abs(dy) > 4:
            self._did_drag = True
        window = self.window()
        if window is not None:
            window.setFrameOrigin_(NSMakePoint(self._window_start.x + dx, self._window_start.y + dy))

    def mouseUp_(self, event) -> None:
        if not self._did_drag and self.click_handler is not None:
            self.click_handler()
        self._drag_start = None
        self._window_start = None
        self._did_drag = False

    def set_size(self, size: float) -> None:
        self.setFrame_(NSMakeRect(self.frame().origin.x, self.frame().origin.y, size, size))
        self.layer().setCornerRadius_(size / 2)
        face_width = max(size * 0.74, 44)
        face_height = max(size * 0.42, 32)
        self.face_label.setFrame_(NSMakeRect((size - face_width) / 2, size * 0.21, face_width, face_height))
        self.face_label.setFont_(NSFont.systemFontOfSize_weight_(max(size * 0.24, 22), 0.62))

    def set_stage(self, stage: str) -> None:
        self._stage = stage
        faces = {
            "idle": "•ᴗ•",
            "recording": "●ᴗ●",
            "loading_model": "•◡•",
            "streaming": "•⌄•",
            "transcribing": "•…•",
            "transcribed": "•…•",
            "done": "•‿•",
            "error": "•︵•",
        }
        accent = _style.STAGE_COLORS.get(stage, _style.STAGE_IDLE)
        border_alpha = 0.28 if stage == "idle" else 0.85
        self.layer().setBackgroundColor_(_color(_style.ORB_BODY).CGColor())
        self.layer().setBorderWidth_(1.4 if stage == "idle" else 2.0)
        self.layer().setBorderColor_(_color(accent, border_alpha).CGColor())
        self.layer().setShadowColor_(_color(accent).CGColor())
        self.face_label.setStringValue_(faces.get(stage, "•ᴗ•"))

    def animate_(self, _timer) -> None:
        stage = getattr(self, "_stage", "idle")
        t = time.perf_counter() - getattr(self, "_t0", time.perf_counter())
        if stage in ("recording", "streaming"):
            pulse = 0.5 + 0.5 * __import__("math").sin(t * 7.5)
            self.layer().setShadowRadius_(20.0 + 10.0 * pulse)
            self.layer().setShadowOpacity_(0.42 + 0.26 * pulse)
            self.layer().setBorderWidth_(2.0 + 1.2 * pulse)
        elif stage in ("transcribing", "transcribed", "loading_model"):
            pulse = 0.5 + 0.5 * __import__("math").sin(t * 3.2)
            self.layer().setShadowRadius_(18.0 + 5.0 * pulse)
            self.layer().setShadowOpacity_(_style.GLOW_ALPHA_WORKING_MIN + 0.14 * pulse)
            self.layer().setBorderWidth_(2.0)
        else:
            self.layer().setShadowRadius_(10.0)
            self.layer().setShadowOpacity_(0.10)
            self.layer().setBorderWidth_(1.4)


class AppBadgeView(NSView):
    def initWithFrame_(self, frame):  # type: ignore[override]
        self = objc.super(AppBadgeView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setWantsLayer_(True)
        self.setHidden_(True)
        self._label = "?"
        self._accent = _style.STAGE_IDLE
        self._icon = None
        return self

    def setBadge_(self, payload) -> None:
        label, stage, icon = payload
        self._label = (label or "?")[:1].upper()
        self._accent = _style.STAGE_COLORS.get(stage, _style.STAGE_IDLE)
        self._icon = icon
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect) -> None:  # type: ignore[override]
        objc.super(AppBadgeView, self).drawRect_(rect)
        bounds = self.bounds()
        accent = _color(self._accent)
        shadow = _color("#000000", 0.35)

        # Coiled telephone cord from orb bottom to app icon.
        cord = NSBezierPath.bezierPath()
        start_x = bounds.size.width / 2
        y0 = bounds.size.height - 4
        y1 = 36
        cord.moveToPoint_((start_x, y0))
        steps = 34
        import math

        for i in range(1, steps + 1):
            tpos = i / steps
            amp = 7.0 * math.sin(tpos * math.pi)
            x = start_x + math.sin(tpos * math.pi * 10) * amp
            y = y0 + (y1 - y0) * tpos
            cord.lineToPoint_((x, y))
        shadow.setStroke()
        cord.setLineWidth_(4.0)
        cord.stroke()
        accent.setStroke()
        cord.setLineWidth_(2.4)
        cord.stroke()

        # App icon ring.
        cx = bounds.size.width / 2
        cy = 18
        r = 15
        ring_rect = NSMakeRect(cx - r, cy - r, r * 2, r * 2)
        bg = NSBezierPath.bezierPathWithOvalInRect_(ring_rect)
        _color("#0C1327", 0.92).setFill()
        bg.fill()
        accent.setStroke()
        bg.setLineWidth_(2.0)
        bg.stroke()

        if self._icon is not None:
            try:
                self._icon.drawInRect_(NSMakeRect(cx - 11, cy - 11, 22, 22))
                return
            except Exception:  # noqa: BLE001
                pass
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_weight_(15, 0.7),
            NSForegroundColorAttributeName: _color(_style.TEXT),
        }
        NSString.stringWithString_(self._label).drawInRect_withAttributes_(
            NSMakeRect(cx - 10, cy - 10, 20, 20),
            attrs,
        )


class BubbleBodyView(NSView):
    PAD_X = 16
    PAD_Y = 12
    TAIL_H = 12
    TAIL_W = 18
    RADIUS = 15
    ACCENT_W = 5

    def initWithFrame_(self, frame):  # type: ignore[override]
        self = objc.super(BubbleBodyView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.setWantsLayer_(True)
        self._tail_side = "left"
        self._accent = _style.STAGE_IDLE
        return self

    def setSpec_(self, payload) -> None:
        _kind, accent, tail_side = payload
        self._accent = accent or _style.STAGE_IDLE
        self._tail_side = tail_side or "left"
        self.setNeedsDisplay_(True)

    def _body_rect(self):
        b = self.bounds()
        x = self.TAIL_H if self._tail_side == "left" else 0
        y = self.TAIL_H if self._tail_side == "bottom" else 0
        w = b.size.width - (self.TAIL_H if self._tail_side in ("left", "right") else 0)
        h = b.size.height - (self.TAIL_H if self._tail_side in ("top", "bottom") else 0)
        if self._tail_side == "right":
            x = 0
        if self._tail_side == "top":
            y = 0
        return NSMakeRect(x, y, w, h)

    def drawRect_(self, rect) -> None:  # type: ignore[override]
        objc.super(BubbleBodyView, self).drawRect_(rect)
        body = self._body_rect()
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(body, self.RADIUS, self.RADIUS)
        cy = body.origin.y + body.size.height / 2
        cx = body.origin.x + body.size.width / 2
        tail = NSBezierPath.bezierPath()
        if self._tail_side == "left":
            x = body.origin.x
            tail.moveToPoint_((x, cy - self.TAIL_W / 2))
            tail.lineToPoint_((x, cy + self.TAIL_W / 2))
            tail.lineToPoint_((x - self.TAIL_H, cy))
        elif self._tail_side == "right":
            x = body.origin.x + body.size.width
            tail.moveToPoint_((x, cy - self.TAIL_W / 2))
            tail.lineToPoint_((x, cy + self.TAIL_W / 2))
            tail.lineToPoint_((x + self.TAIL_H, cy))
        elif self._tail_side == "top":
            y = body.origin.y + body.size.height
            tail.moveToPoint_((cx - self.TAIL_W / 2, y))
            tail.lineToPoint_((cx + self.TAIL_W / 2, y))
            tail.lineToPoint_((cx, y + self.TAIL_H))
        else:
            y = body.origin.y
            tail.moveToPoint_((cx - self.TAIL_W / 2, y))
            tail.lineToPoint_((cx + self.TAIL_W / 2, y))
            tail.lineToPoint_((cx, y - self.TAIL_H))
        tail.closePath()
        path.appendBezierPath_(tail)

        _color(_style.CARD_BG, 0.98).setFill()
        path.fill()
        _color(_style.BORDER).setStroke()
        path.setLineWidth_(1.0)
        path.stroke()

        accent = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(body.origin.x + 7, body.origin.y + 7, 3, body.size.height - 14),
            1.5,
            1.5,
        )
        _color(self._accent).setFill()
        accent.fill()


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
        self.rephrased_view = None
        self.context_view = None
        self.history_view = None
        self.badge_view = None
        self.error_label = None
        self._bubble_panels: dict[str, tuple] = {}
        self._settings_window = None
        self._settings_fields = {}
        self._history: list[dict[str, str]] = []
        self._last_history_signature = ""
        self._last_bubble_signature = ""
        self._last_seen_stage = "idle"
        self._bubble_hide_timers: dict[str, object] = {}
        self._preferred_target: AppTarget | None = None
        self._full_frame = None
        self._full_style_mask = None
        self._collapsed = False
        self._content_subviews = []
        return self

    def build_window(self) -> None:
        width = 400
        height = 720
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskFullSizeContentView
            | NSWindowStyleMaskResizable
        )
        window = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(60, 700, width, height),
            style,
            NSBackingStoreBuffered,
            False,
        )
        window.setTitle_(t("window.title"))
        window.setReleasedWhenClosed_(False)
        window.setLevel_(NSScreenSaverWindowLevel)
        window.setMovableByWindowBackground_(True)
        window.setTitleVisibility_(1)
        window.setTitlebarAppearsTransparent_(True)
        window.setDelegate_(self)
        window.setFloatingPanel_(True)
        window.setBecomesKeyOnlyIfNeeded_(False)
        window.setHidesOnDeactivate_(False)
        window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        window.setBackgroundColor_(_color(_style.CARD_BG, 0.96))
        self.window = window
        self._full_style_mask = style

        content = window.contentView()

        sprite = SpriteOrbView.alloc().initWithFrame_(NSMakeRect(120, 510, 160, 160))
        sprite.click_handler = self.expandOverlay_
        content.addSubview_(sprite)
        self.sprite = sprite

        status = NSTextField.labelWithString_("IDLE")
        status.setFrame_(NSMakeRect(50, 475, 300, 28))
        status.setAlignment_(1)
        status.setFont_(NSFont.systemFontOfSize_weight_(18, 0.65))
        status.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(0.92, 1.0))
        content.addSubview_(status)
        self.status_label = status

        hotkey_text = self.state.snapshot()["hotkey"]
        tip = NSTextField.labelWithString_(t("label.hotkey", hotkey=hotkey_text))
        tip.setFrame_(NSMakeRect(50, 450, 300, 22))
        tip.setAlignment_(1)
        tip.setFont_(NSFont.systemFontOfSize_(12))
        tip.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.69, 0.88, 1.0))
        content.addSubview_(tip)
        self.tip_label = tip

        start_button = NSButton.alloc().initWithFrame_(NSMakeRect(97, 414, 44, 40))
        _style_icon_button(start_button, "🎙", t("btn.start.tip"), "mic.fill")
        start_button.setTarget_(self)
        start_button.setAction_("startRecording:")
        content.addSubview_(start_button)

        stop_button = NSButton.alloc().initWithFrame_(NSMakeRect(151, 414, 44, 40))
        _style_icon_button(stop_button, "■", t("btn.stop.tip"), "stop.fill")
        stop_button.setTarget_(self)
        stop_button.setAction_("stopRecording:")
        content.addSubview_(stop_button)

        shrink_button = NSButton.alloc().initWithFrame_(NSMakeRect(205, 414, 44, 40))
        _style_icon_button(shrink_button, "↙", t("btn.shrink.tip"), "arrow.down.right.and.arrow.up.left")
        shrink_button.setTarget_(self)
        shrink_button.setAction_("collapseOverlay:")
        content.addSubview_(shrink_button)

        quit_button = NSButton.alloc().initWithFrame_(NSMakeRect(259, 414, 44, 40))
        _style_icon_button(quit_button, "×", t("btn.quit.tip"), "xmark")
        quit_button.setTarget_(self)
        quit_button.setAction_("quitOverlay:")
        content.addSubview_(quit_button)

        settings_button = NSButton.alloc().initWithFrame_(NSMakeRect(118, 382, 82, 24))
        settings_button.setTitle_(t("toggle.settings").replace("⚙ ", ""))
        settings_button.setBezelStyle_(1)
        settings_button.setTarget_(self)
        settings_button.setAction_("openSettings:")
        content.addSubview_(settings_button)

        azure_button = NSButton.alloc().initWithFrame_(NSMakeRect(208, 382, 82, 24))
        azure_button.setTitle_("Azure")
        azure_button.setBezelStyle_(1)
        azure_button.setTarget_(self)
        azure_button.setAction_("signInAzure:")
        content.addSubview_(azure_button)

        context_title = NSTextField.labelWithString_(t("label.active_context"))
        context_title.setFrame_(NSMakeRect(28, 355, 160, 18))
        context_title.setFont_(NSFont.systemFontOfSize_weight_(12, 0.6))
        context_title.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.69, 0.88, 1.0))
        content.addSubview_(context_title)

        context_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(28, 302, 344, 48))
        context_scroll.setBorderType_(0)
        context_scroll.setHasVerticalScroller_(True)
        context_scroll.setDrawsBackground_(False)
        context_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 344, 48))
        context_view.setEditable_(False)
        context_view.setSelectable_(True)
        context_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(11, 0.4))
        context_view.setBackgroundColor_(_color(_style.FIELD_BG, 0.95))
        context_view.setTextColor_(_color("#B9C6E4"))
        context_view.setString_(t("ph.context"))
        context_scroll.setDocumentView_(context_view)
        content.addSubview_(context_scroll)
        self.context_view = context_view

        transcript_title = NSTextField.labelWithString_(t("label.raw_transcript"))
        transcript_title.setFrame_(NSMakeRect(28, 275, 160, 18))
        transcript_title.setFont_(NSFont.systemFontOfSize_weight_(12, 0.6))
        transcript_title.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.69, 0.88, 1.0))
        content.addSubview_(transcript_title)

        copy_raw_button = NSButton.alloc().initWithFrame_(NSMakeRect(330, 272, 32, 24))
        _style_icon_button(copy_raw_button, "⧉", t("btn.copy_raw.tip"))
        copy_raw_button.setFont_(NSFont.systemFontOfSize_weight_(14, 0.55))
        copy_raw_button.setTarget_(self)
        copy_raw_button.setAction_("copyRaw:")
        content.addSubview_(copy_raw_button)

        transcript_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(28, 212, 344, 58))
        transcript_scroll.setBorderType_(0)
        transcript_scroll.setHasVerticalScroller_(True)
        transcript_scroll.setDrawsBackground_(False)

        transcript_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 344, 58))
        transcript_view.setEditable_(False)
        transcript_view.setSelectable_(True)
        transcript_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 0.4))
        transcript_view.setBackgroundColor_(_color(_style.PANEL_BG, 0.95))
        transcript_view.setTextColor_(_color("#ECECEC"))
        transcript_view.setString_(t("ph.transcript"))
        transcript_scroll.setDocumentView_(transcript_view)
        content.addSubview_(transcript_scroll)
        self.transcript_view = transcript_view

        rephrased_title = NSTextField.labelWithString_(t("label.polished"))
        rephrased_title.setFrame_(NSMakeRect(28, 185, 160, 18))
        rephrased_title.setFont_(NSFont.systemFontOfSize_weight_(12, 0.6))
        rephrased_title.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.69, 0.88, 1.0))
        content.addSubview_(rephrased_title)

        copy_polished_button = NSButton.alloc().initWithFrame_(NSMakeRect(330, 182, 32, 24))
        _style_icon_button(copy_polished_button, "⧉", t("btn.copy_polished.tip"))
        copy_polished_button.setFont_(NSFont.systemFontOfSize_weight_(14, 0.55))
        copy_polished_button.setTarget_(self)
        copy_polished_button.setAction_("copyPolished:")
        content.addSubview_(copy_polished_button)

        rephrased_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(28, 122, 344, 58))
        rephrased_scroll.setBorderType_(0)
        rephrased_scroll.setHasVerticalScroller_(True)
        rephrased_scroll.setDrawsBackground_(False)

        rephrased_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 344, 58))
        rephrased_view.setEditable_(False)
        rephrased_view.setSelectable_(True)
        rephrased_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 0.4))
        rephrased_view.setBackgroundColor_(_color(_style.PANEL_BG, 0.95))
        rephrased_view.setTextColor_(_color("#ECECEC"))
        rephrased_view.setString_(t("ph.polished"))
        rephrased_scroll.setDocumentView_(rephrased_view)
        content.addSubview_(rephrased_scroll)
        self.rephrased_view = rephrased_view

        history_title = NSTextField.labelWithString_(t("toggle.history"))
        history_title.setFrame_(NSMakeRect(28, 95, 160, 18))
        history_title.setFont_(NSFont.systemFontOfSize_weight_(12, 0.6))
        history_title.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.69, 0.88, 1.0))
        content.addSubview_(history_title)

        history_scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(28, 48, 344, 42))
        history_scroll.setBorderType_(0)
        history_scroll.setHasVerticalScroller_(True)
        history_scroll.setDrawsBackground_(False)
        history_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 344, 42))
        history_view.setEditable_(False)
        history_view.setSelectable_(True)
        history_view.setFont_(NSFont.monospacedSystemFontOfSize_weight_(10, 0.4))
        history_view.setBackgroundColor_(_color("#0C1327", 0.95))
        history_view.setTextColor_(_color("#C7D2E8"))
        history_view.setString_(t("label.history_empty"))
        history_scroll.setDocumentView_(history_view)
        content.addSubview_(history_scroll)
        self.history_view = history_view

        badge_view = AppBadgeView.alloc().initWithFrame_(NSMakeRect(38, 0, 84, 38))
        content.addSubview_(badge_view)
        self.badge_view = badge_view

        error_title = NSTextField.labelWithString_(t("label.status_error"))
        error_title.setFrame_(NSMakeRect(28, 24, 140, 18))
        error_title.setFont_(NSFont.systemFontOfSize_weight_(12, 0.6))
        error_title.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.69, 0.88, 1.0))
        content.addSubview_(error_title)

        error_label = NSTextField.labelWithString_(str(self.state.snapshot()["error"]))
        error_label.setFrame_(NSMakeRect(28, 4, 344, 18))
        error_label.setFont_(NSFont.systemFontOfSize_(12))
        error_label.setTextColor_(_color(_style.ERROR_TEXT))
        error_label.setLineBreakMode_(2)
        error_label.setAllowsDefaultTighteningForTruncation_(True)
        content.addSubview_(error_label)
        self.error_label = error_label
        self._content_subviews = [view for view in content.subviews() if view not in (sprite, badge_view)]

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
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.05,
            self.sprite,
            "animate:",
            None,
            True,
        )
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.9,
            self,
            "maybeShowGreeting:",
            None,
            False,
        )
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.2,
            self,
            "checkAzureStatus:",
            None,
            False,
        )

    def refreshState_(self, _timer) -> None:
        snapshot = self.state.snapshot()
        stage = str(snapshot.get("stage", "idle"))
        raw_text = str(snapshot.get("raw_text", "") or snapshot.get("plain_text", "") or t("ph.transcript"))
        rephrased_text = str(snapshot.get("rephrased_text", "") or t("ph.polished"))
        error = str(snapshot.get("error", "") or t("status.ready"))
        target_app = str(snapshot.get("target_app", "")).strip()
        self._update_preferred_target()

        assert self.sprite is not None
        assert self.status_label is not None
        assert self.transcript_view is not None
        assert self.rephrased_view is not None
        assert self.context_view is not None
        assert self.history_view is not None
        assert self.error_label is not None

        self.sprite.set_stage(stage)
        self._refresh_collapsed_badge()
        self._maybe_show_stage_bubble(stage, snapshot)
        status_text = stage.upper()
        if stage == "done" and target_app:
            status_text = f"DONE -> {target_app}"
        self.status_label.setStringValue_(status_text)
        self.transcript_view.setString_(raw_text)
        self.rephrased_view.setString_(rephrased_text)
        self.context_view.setString_(self._context_text())
        self._maybe_add_history(snapshot)
        self.history_view.setString_(self._history_text())
        self.error_label.setStringValue_(error)

    def windowWillClose_(self, _notification) -> None:
        self.listener.stop()
        self._stop_session_quietly()
        NSApp.stop_(None)

    def windowShouldClose_(self, _sender) -> bool:
        self.quitOverlay_(None)
        return True

    def startRecording_(self, _sender) -> None:
        self.state.update({"stage": "recording", "error": t("status.recording")})
        threading.Thread(target=self._safe_start_recording, daemon=True).start()

    def stopRecording_(self, _sender) -> None:
        self.state.update({"stage": "transcribing", "error": t("status.finishing")})
        threading.Thread(target=self._safe_stop_recording, daemon=True).start()

    def quitOverlay_(self, _sender) -> None:
        self.state.update({"error": t("btn.quit.tip")})
        self.listener.stop()
        self._stop_session_quietly()
        if self.window is not None:
            self.window.orderOut_(None)
        NSApp.terminate_(None)

    def relaunchOverlay_(self, _sender) -> None:
        self.state.update({"error": t("btn.relaunch.tip")})
        subprocess.Popen([sys.executable] + sys.argv)
        self.quitOverlay_(None)

    def copyRaw_(self, _sender) -> None:
        raw = str(self.state.snapshot().get("raw_text", "") or "").strip()
        self._copy_text(raw, t("label.raw_transcript"))

    def copyPolished_(self, _sender) -> None:
        snap = self.state.snapshot()
        text = str(snap.get("rephrased_text", "") or snap.get("raw_text", "") or "").strip()
        self._copy_text(text, t("label.polished"))

    def openSettings_(self, _sender) -> None:
        self._show_settings_window()

    def signInAzure_(self, _sender) -> None:
        self.state.update({"error": t("msg.signin_browser")})
        threading.Thread(target=self._safe_sign_in, daemon=True).start()

    def checkAzureStatus_(self, _timer) -> None:
        cfg = _config.load_config(reload=True)
        if cfg.get("backend") != "azure" and cfg.get("polish_engine") != "azure":
            return
        threading.Thread(target=self._safe_auth_status, daemon=True).start()

    def maybeShowGreeting_(self, _timer) -> None:
        try:
            cfg = _config.load_config(reload=True)
            if cfg.get("first_launch_done"):
                return
            message = t("bubble.greeting", hotkey=str(self.state.snapshot().get("hotkey", "")).upper())
            if self._collapsed:
                self._show_bubble(make_bubble(message, kind=BubbleKind.GREETING, accent="#6EA8FC"))
            else:
                self.state.update({"error": message})
            _config.save_config({"first_launch_done": True})
        except Exception:  # noqa: BLE001
            return

    def _copy_text(self, text: str, label: str) -> None:
        if not text:
            self.state.update({"error": t("msg.field_empty", label=label)})
            return
        try:
            pyperclip.copy(text)
            self.state.update({"error": t("msg.copied_field", label=label)})
        except pyperclip.PyperclipException as exc:
            self.state.update({"error": t("status.copy_failed", error=exc)})

    def _safe_sign_in(self) -> None:
        try:
            from . import azure_client

            status = azure_client.sign_in()
            acct = status.get("account") or ""
            sep = "：" if set_language(_config.load_config().get("ui_language")) == "zh" else ": "
            self.state.update({"error": t("msg.signed_in", acct=f"{sep}{acct}" if acct else "")})
        except BaseException as exc:  # noqa: BLE001
            self.state.update({"stage": "error", "error": t("msg.signin_failed", message=exc)})

    def _safe_auth_status(self) -> None:
        try:
            from . import azure_client

            status = azure_client.auth_status()
        except BaseException as exc:  # noqa: BLE001
            self.state.update({"error": t("msg.signin_failed", message=exc)})
            return
        if not status.get("signed_in", False):
            self.state.update({"error": t("msg.not_signed_in")})

    def _context_text(self) -> str:
        target = self._preferred_target
        if target is None:
            return t("ph.context")
        lines = [target.name or t("label.app_unknown")]
        if target.bundle_id:
            lines.append(target.bundle_id)
        lines.append(f"pid={target.pid}")
        cfg = _config.load_config()
        azure = cfg.get("azure") or {}
        if cfg.get("backend") == "azure":
            lines.append(f"azure.transcribe_mode={azure.get('transcribe_mode', 'batch')}")
        return "\n".join(lines)

    def _maybe_add_history(self, snapshot: dict[str, object]) -> None:
        if str(snapshot.get("stage", "")) != "done":
            return
        raw = str(snapshot.get("raw_text", "") or snapshot.get("plain_text", "") or "").strip()
        polished = str(snapshot.get("rephrased_text", "") or raw).strip()
        if not (raw or polished):
            return
        sig = f"{snapshot.get('audio_path')}|{raw}|{polished}"
        if sig == self._last_history_signature:
            return
        self._last_history_signature = sig
        app = str(snapshot.get("target_app", "") or "").strip()
        self._history.insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "app": app,
            "raw": raw,
            "polished": polished,
        })
        del self._history[30:]

    def _history_text(self) -> str:
        if not self._history:
            return t("label.history_empty")
        lines: list[str] = []
        for item in self._history[:6]:
            text = item.get("polished") or item.get("raw") or ""
            preview = text.replace("\n", " ")
            if len(preview) > 72:
                preview = preview[:72] + "…"
            meta = item.get("time", "")
            if item.get("app"):
                meta = f"{meta} · {item['app']}"
            lines.append(f"{meta}  {preview}")
        return "\n".join(lines)

    def _maybe_show_stage_bubble(self, stage: str, snapshot: dict[str, object]) -> None:
        if not self._collapsed:
            self._last_seen_stage = stage
            return
        if stage in ("recording", "streaming"):
            ctx = self._context_text()
            if ctx and ctx != t("ph.context"):
                # Context is a live companion to the app badge: keep it visible
                # while recording/streaming and hide it only when recording ends.
                self._show_bubble(make_bubble(ctx, kind=BubbleKind.CONTEXT, stage=stage, duration_ms=0))
        elif self._last_seen_stage in ("recording", "streaming"):
            self._hide_bubble_key(BubbleKind.CONTEXT.value)
        if stage == "streaming":
            text = str(snapshot.get("plain_text", "") or snapshot.get("raw_text", "") or "").strip()
            if text:
                self._show_bubble(make_bubble(text, kind=BubbleKind.SPEECH, stage=stage, duration_ms=20000))
        if stage == "done":
            text = str(snapshot.get("rephrased_text", "") or snapshot.get("raw_text", "") or snapshot.get("plain_text", "") or "").strip()
            sig = f"{snapshot.get('audio_path')}|{text}"
            if text and sig != self._last_bubble_signature:
                self._last_bubble_signature = sig
                self._show_bubble(make_bubble(text, kind=BubbleKind.SPEECH, stage=stage, duration_ms=10000))
        self._last_seen_stage = stage

    def _show_settings_window(self) -> None:
        if self._settings_window is not None:
            self._settings_window.makeKeyAndOrderFront_(None)
            self._settings_window.orderFrontRegardless()
            return
        cfg = _config.load_config(reload=True)
        azure = cfg.get("azure") or {}
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(120, 520, 460, 620),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_(t("toggle.settings"))
        panel.setReleasedWhenClosed_(False)
        content = panel.contentView()
        self._settings_fields = {}
        rows = [
            ("ui_language", cfg.get("ui_language", "auto")),
            ("hotkey", cfg.get("hotkey", "f9")),
            ("input_device", cfg.get("input_device", "")),
            ("start_collapsed", str(bool(cfg.get("start_collapsed", True))).lower()),
            ("language_preference", cfg.get("language_preference", "zh-en")),
            ("language", cfg.get("language", "zh")),
            ("backend", cfg.get("backend", "faster-whisper")),
            ("model", cfg.get("model", "small")),
            ("hf_endpoint", cfg.get("hf_endpoint", "https://hf-mirror.com")),
            ("mlx_model", cfg.get("mlx_model", "")),
            ("polish", cfg.get("polish", "off")),
            ("polish_engine", cfg.get("polish_engine", "rules")),
            ("copy_to_clipboard", str(bool(cfg.get("copy_to_clipboard", False))).lower()),
            ("paste_to_active_app", str(bool(cfg.get("paste_to_active_app", True))).lower()),
            ("submit_to_active_app", str(bool(cfg.get("submit_to_active_app", False))).lower()),
            ("azure.auth", azure.get("auth", "aad")),
            ("azure.transcribe_mode", azure.get("transcribe_mode", "batch")),
            ("azure.endpoint", azure.get("endpoint", "")),
        ]
        y = 570
        for key, value in rows:
            label = NSTextField.labelWithString_(t(f"settings.field.{key}"))
            label.setFrame_(NSMakeRect(18, y + 4, 150, 20))
            content.addSubview_(label)
            field = NSTextField.alloc().initWithFrame_(NSMakeRect(175, y, 255, 24))
            field.setStringValue_(str(value))
            content.addSubview_(field)
            self._settings_fields[key] = field
            y -= 28
        hint = NSTextField.labelWithString_("azure.transcribe_mode: batch | stream | realtime")
        hint.setFrame_(NSMakeRect(18, 42, 410, 18))
        hint.setFont_(NSFont.systemFontOfSize_(11))
        hint.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.62, 0.69, 0.88, 1.0))
        content.addSubview_(hint)
        save = NSButton.alloc().initWithFrame_(NSMakeRect(270, 12, 76, 26))
        save.setTitle_(t("btn.save"))
        save.setBezelStyle_(1)
        save.setTarget_(self)
        save.setAction_("saveSettings:")
        content.addSubview_(save)
        close = NSButton.alloc().initWithFrame_(NSMakeRect(354, 12, 76, 26))
        close.setTitle_(t("btn.quit.tip"))
        close.setBezelStyle_(1)
        close.setTarget_(self)
        close.setAction_("closeSettings:")
        content.addSubview_(close)
        self._settings_window = panel
        panel.makeKeyAndOrderFront_(None)
        panel.orderFrontRegardless()

    def saveSettings_(self, _sender) -> None:
        def _text(key: str) -> str:
            field = self._settings_fields.get(key)
            return str(field.stringValue()).strip() if field is not None else ""

        def _bool(value: str) -> bool:
            return value.strip().lower() in ("1", "true", "yes", "on")

        updates = {
            "ui_language": _text("ui_language") or "auto",
            "hotkey": _text("hotkey") or "f9",
            "input_device": _text("input_device"),
            "start_collapsed": _bool(_text("start_collapsed")),
            "language_preference": _text("language_preference") or "zh-en",
            "language": _text("language") or "zh",
            "backend": _text("backend") or "faster-whisper",
            "model": _text("model") or "small",
            "hf_endpoint": _text("hf_endpoint") or "https://hf-mirror.com",
            "mlx_model": _text("mlx_model"),
            "polish": _text("polish") or "off",
            "polish_engine": _text("polish_engine") or "rules",
            "copy_to_clipboard": _bool(_text("copy_to_clipboard")),
            "paste_to_active_app": _bool(_text("paste_to_active_app")),
            "submit_to_active_app": _bool(_text("submit_to_active_app")),
            "azure": {
                "auth": _text("azure.auth") or "aad",
                "transcribe_mode": _text("azure.transcribe_mode") or "batch",
                "endpoint": _text("azure.endpoint"),
            },
        }
        try:
            path = _config.save_config(updates)
            self._apply_settings(updates)
            self.state.update({"error": t("msg.settings_saved", name=path.name)})
        except OSError as exc:
            self.state.update({"stage": "error", "error": t("msg.settings_save_failed", error=exc)})

    def closeSettings_(self, _sender) -> None:
        if self._settings_window is not None:
            self._settings_window.orderOut_(None)

    def _apply_settings(self, updates: dict[str, object]) -> None:
        set_language(str(updates.get("ui_language") or "auto"))
        self.session.language = str(updates.get("language") or self.session.language)
        self.session.model_name = str(updates.get("model") or self.session.model_name)
        self.session.backend = str(updates.get("backend") or self.session.backend)
        self.session.mlx_model = str(updates.get("mlx_model") or self.session.mlx_model)
        self.session.hf_endpoint = str(updates.get("hf_endpoint") or self.session.hf_endpoint)
        self.session.polish = str(updates.get("polish") or self.session.polish)
        self.session.polish_engine = str(updates.get("polish_engine") or self.session.polish_engine)
        self.session.language_preference = str(updates.get("language_preference") or self.session.language_preference)
        self.session.copy_to_clipboard = bool(updates.get("copy_to_clipboard"))
        self.session.paste_to_active_app = bool(updates.get("paste_to_active_app"))
        self.session.submit_to_active_app = bool(updates.get("submit_to_active_app"))
        new_hotkey = str(updates.get("hotkey") or self.state.snapshot().get("hotkey") or "f9")
        if new_hotkey != self.state.snapshot().get("hotkey"):
            try:
                self.listener.stop()
                self.listener = _make_hotkey_listener(new_hotkey, self.session)
                self.listener.start()
                self.state.update({"hotkey": new_hotkey})
                if self.tip_label is not None:
                    self.tip_label.setStringValue_(t("label.hotkey", hotkey=new_hotkey))
            except BaseException as exc:  # noqa: BLE001
                self.state.update({"stage": "error", "error": f"Hotkey update failed: {exc}"})

    def collapseOverlay_(self, _sender) -> None:
        if self.window is None or self.sprite is None or self._collapsed:
            return
        self._collapsed = True
        self._full_frame = self.window.frame()
        self._full_style_mask = self.window.styleMask()
        for view in self._content_subviews:
            view.setHidden_(True)
        self._refresh_collapsed_badge()
        self.window.setStyleMask_(NSWindowStyleMaskBorderless)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        current = self.window.frame()
        collapsed_w = 160
        collapsed_h = 188
        self.window.setFrame_display_(
            NSMakeRect(current.origin.x, current.origin.y + current.size.height - collapsed_h, collapsed_w, collapsed_h),
            True,
        )
        self.sprite.setFrame_(NSMakeRect(40, 64, 80, 80))
        self.sprite.set_size(80)
        self.sprite.setToolTip_("Click to expand")
        self._refresh_collapsed_badge()
        self.window.orderFrontRegardless()

    def expandOverlay_(self, _sender=None) -> None:
        if self.window is None or self.sprite is None or not self._collapsed:
            return
        self._collapsed = False
        self._hide_bubble()
        for view in self._content_subviews:
            view.setHidden_(False)
        if self.badge_view is not None:
            self.badge_view.setHidden_(True)
        if self._full_style_mask is not None:
            self.window.setStyleMask_(self._full_style_mask)
        self.window.setOpaque_(True)
        self.window.setBackgroundColor_(_color(_style.CARD_BG, 0.96))
        if self._full_frame is not None:
            self.window.setFrame_display_(self._full_frame, True)
        self.sprite.setFrame_(NSMakeRect(120, 510, 160, 160))
        self.sprite.set_size(160)
        self.sprite.setToolTip_("")
        self.window.orderFrontRegardless()

    def _refresh_collapsed_badge(self) -> None:
        if self.badge_view is None or not self._collapsed:
            return
        stage = str(self.state.snapshot().get("stage", "idle"))
        if stage not in ("recording", "streaming"):
            self.badge_view.setHidden_(True)
            return
        target = self._preferred_target
        label = "?"
        icon = None
        if target is not None:
            label = (target.name or target.bundle_id or "?").strip()[:1] or "?"
            icon = self._app_icon(target)
        self.badge_view.setBadge_((label, stage, icon))
        self.badge_view.setHidden_(False)

    def _app_icon(self, target: AppTarget):
        try:
            if target.bundle_id:
                apps = NSWorkspace.sharedWorkspace().runningApplications()
                for app in apps:
                    if str(app.bundleIdentifier() or "") == target.bundle_id:
                        icon = app.icon()
                        if icon is not None:
                            return icon
        except Exception:  # noqa: BLE001
            return None
        return None

    def _bubble_panel(self, key: str):
        existing = self._bubble_panels.get(key)
        if existing is not None:
            return existing
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 320, 90),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(NSScreenSaverWindowLevel)
        panel.setReleasedWhenClosed_(False)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        body = BubbleBodyView.alloc().initWithFrame_(NSMakeRect(0, 0, 320, 90))
        label = NSTextView.alloc().initWithFrame_(NSMakeRect(28, 14, 278, 62))
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setDrawsBackground_(False)
        label.setFont_(NSFont.systemFontOfSize_weight_(13, 0.55))
        label.setTextColor_(_color(_style.TEXT))
        body.addSubview_(label)
        panel.contentView().addSubview_(body)
        entry = (panel, body, label)
        self._bubble_panels[key] = entry
        return entry

    def _show_bubble(self, spec: BubbleSpec | str, *, kind: str = "speech", duration: float = 9.0) -> None:
        if isinstance(spec, str):
            spec = make_bubble(spec, kind=kind, stage=str(self.state.snapshot().get("stage", "idle")), duration_ms=int(duration * 1000))
        if self.window is None or not spec.text.strip():
            return
        key = spec.kind.value
        panel, body, label = self._bubble_panel(key)
        label.setString_(spec.text)
        frame = self.window.frame()
        width = 320
        height = 90
        panel.setFrame_display_(NSMakeRect(0, 0, width, height), True)
        body.setFrame_(NSMakeRect(0, 0, width, height))
        label.setFrame_(NSMakeRect(28, 14, width - 42, height - 28))
        # Qt semantics: speech bubble anchors to pet; context bubble anchors to app badge.
        anchor_x = frame.origin.x + frame.size.width
        anchor_y = frame.origin.y + frame.size.height - 60
        if spec.anchor.value == "app_badge":
            anchor_x = frame.origin.x + frame.size.width / 2
            anchor_y = frame.origin.y + 24
        tail_side = "left"
        x = anchor_x - 2
        y = anchor_y - height / 2
        screen = NSScreen.mainScreen()
        if screen is not None:
            avail = screen.visibleFrame()
            if x + width > avail.origin.x + avail.size.width - 6:
                x = frame.origin.x - width + 4
                tail_side = "right"
            x = max(avail.origin.x + 6, min(x, avail.origin.x + avail.size.width - width - 6))
            y = max(avail.origin.y + 6, min(y, avail.origin.y + avail.size.height - height - 6))
        panel.setFrame_display_(
            NSMakeRect(x, y, width, height),
            True,
        )
        body.setSpec_((spec.kind.value, spec.accent, tail_side))
        self._show_panel_animated(panel)
        if spec.duration_ms > 0:
            selector = "hideSpeechBubble:" if key == BubbleKind.SPEECH.value else "hideGreetingBubble:"
            old_timer = self._bubble_hide_timers.get(key)
            if old_timer is not None:
                try:
                    old_timer.invalidate()
                except Exception:  # noqa: BLE001
                    pass
            self._bubble_hide_timers[key] = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                max(0.5, spec.duration_ms / 1000),
                self,
                selector,
                None,
                False,
            )

    def hideBubble_(self, _timer) -> None:
        self._hide_bubble()

    def hideSpeechBubble_(self, _timer) -> None:
        self._hide_bubble_key(BubbleKind.SPEECH.value)

    def hideGreetingBubble_(self, _timer) -> None:
        self._hide_bubble_key(BubbleKind.GREETING.value)

    def _hide_bubble(self) -> None:
        for panel, _body, _label in self._bubble_panels.values():
            panel.orderOut_(None)

    def _hide_bubble_key(self, key: str) -> None:
        self._bubble_hide_timers.pop(key, None)
        entry = self._bubble_panels.get(key)
        if entry is not None:
            entry[0].orderOut_(None)

    def _show_panel_animated(self, panel) -> None:
        was_visible = bool(panel.isVisible())
        if was_visible:
            panel.setAlphaValue_(1.0)
            panel.orderFrontRegardless()
            return
        try:
            panel.setAlphaValue_(0.0)
            panel.orderFrontRegardless()

            def _anim(ctx) -> None:
                ctx.setDuration_(0.16)
                panel.animator().setAlphaValue_(1.0)

            NSAnimationContext.runAnimationGroup_completionHandler_(_anim, None)
        except Exception:  # noqa: BLE001
            panel.setAlphaValue_(1.0)
            panel.orderFrontRegardless()

    def _safe_start_recording(self) -> None:
        try:
            self.session.start_recording()
        except BaseException as exc:  # noqa: BLE001
            self.state.update({"stage": "error", "error": t("status.start_failed", error=exc)})

    def _safe_stop_recording(self) -> None:
        try:
            self.session.stop_recording()
        except BaseException as exc:  # noqa: BLE001
            self.state.update({"stage": "error", "error": t("status.stop_failed", error=exc)})

    def _stop_session_quietly(self) -> None:
        try:
            self.session.stop_if_recording()
        except BaseException as exc:  # noqa: BLE001
            self.state.update({"stage": "error", "error": t("status.stop_failed", error=exc)})

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
    backend: str,
    mlx_model: str,
    copy_to_clipboard: bool | None,
    paste_to_active_app: bool | None,
    submit_to_active_app: bool | None,
    plain: bool,
    save_text: Path | None,
    hf_endpoint: str,
    replacement_pairs: list[str],
    replacements_file: Path | None,
    streaming: bool,
    polish: str,
    context_file: Path | None,
    session_context: bool,
    language_preference: str,
    polish_engine: str,
    ollama_model: str,
) -> None:
    cfg = _config.load_config()
    set_language(cfg.get("ui_language"))
    state = OverlayState(hotkey)
    should_copy, should_paste, should_submit = resolve_delivery_flags(
        cfg, copy_to_clipboard, paste_to_active_app, submit_to_active_app
    )
    native_streaming = streaming or backend in ("faster-whisper", "mlx")
    session = HotkeySession(
        language=language,
        model_name=model_name,
        backend=backend,
        mlx_model=mlx_model,
        copy_to_clipboard=should_copy,
        paste_to_active_app=should_paste,
        submit_to_active_app=should_submit,
        plain=plain,
        save_text=save_text,
        hf_endpoint=hf_endpoint,
        replacement_pairs=replacement_pairs,
        replacements_file=replacements_file,
        status_reporter=state.update,
        streaming=native_streaming,
        polish=polish,
        context_file=context_file,
        session_context=session_context,
        language_preference=language_preference,
        polish_engine=polish_engine,
        ollama_model=ollama_model,
    )
    listener = _make_hotkey_listener(hotkey, session)
    listener.start()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    controller = SpriteOverlayController.alloc().initWithState_session_listener_(state, session, listener)
    session.target_app_getter = controller.get_preferred_target
    controller.build_window()
    controller.show()
    if bool(cfg.get("start_collapsed", True)):
        controller.collapseOverlay_(None)

    print(f"Overlay is running. Press {hotkey} to start/stop recording.", flush=True)
    try:
        app.run()
    finally:
        listener.stop()
        try:
            session.stop_if_recording()
        except BaseException:  # noqa: BLE001
            pass
