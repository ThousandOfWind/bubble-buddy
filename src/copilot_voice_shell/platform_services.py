"""Platform-specific OS services centralised in one place.

All ``sys.platform`` / ``platform.system()`` branching lives here.
Callers import :func:`get_platform_services` and call the returned object;
they never need to check the OS themselves.

Adding a new platform-specific behaviour:
  1. Add a method to the :class:`PlatformServices` Protocol.
  2. Implement it in ``_MacOSServices``, ``_WindowsServices``, and
     ``_FallbackServices``.
  3. Call it from business code via ``get_platform_services().<method>()``.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class FocusInfo:
    """Platform-agnostic snapshot of a focused window / application.

    Fields are populated on a best-effort basis; absent fields default to
    their zero value.

    Attributes:
        name:       Human-readable application name (all platforms).
        bundle_id:  macOS bundle identifier (e.g. ``com.microsoft.VSCode``).
        pid:        Process identifier (macOS).
        hwnd:       Win32 window handle (Windows).
    """

    name: str = ""
    bundle_id: str = ""
    pid: int = 0
    hwnd: int = 0


class PlatformServices(Protocol):
    """Behavioural interface for OS-specific window and input operations."""

    def get_frontmost_window(self, own_window_id: int = 0) -> FocusInfo | None:
        """Return a snapshot of the current foreground window, or ``None``.

        *own_window_id* is the native handle of the calling window so the
        implementation can skip it (avoids returning the overlay itself).
        """

    def restore_focus(self, target: FocusInfo) -> None:
        """Bring *target* back to the foreground."""

    def enforce_topmost(self, window_id: int) -> None:
        """Force the window identified by *window_id* to stay above all others."""

    def paste_keystroke(self, *, submit: bool = False) -> None:
        """Send the platform paste shortcut (⌘V / Ctrl+V).

        Waits briefly before sending to allow focus to settle.
        If *submit* is ``True``, also presses Return afterwards.
        """


# ---------------------------------------------------------------------------
# macOS implementation
# ---------------------------------------------------------------------------

class _MacOSServices:
    def get_frontmost_window(self, own_window_id: int = 0) -> FocusInfo | None:
        try:
            import os as _os
            from AppKit import NSWorkspace  # type: ignore[import-untyped]

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return None
            pid = int(app.processIdentifier())
            if own_window_id and pid == _os.getpid():
                return None
            return FocusInfo(
                name=app.localizedName() or "",
                bundle_id=app.bundleIdentifier() or "",
                pid=pid,
            )
        except BaseException:  # noqa: BLE001
            return None

    def restore_focus(self, target: FocusInfo | None) -> None:
        if target is None:
            return
        try:
            import subprocess
            if target.bundle_id:
                subprocess.run(
                    ["osascript", "-e", f'tell application id "{target.bundle_id}" to activate'],
                    check=False,
                )
            elif target.name:
                subprocess.run(
                    ["osascript", "-e", f'tell application "{target.name}" to activate'],
                    check=False,
                )
        except BaseException:  # noqa: BLE001
            return

    def enforce_topmost(self, window_id: int) -> None:
        try:
            from ctypes import c_void_p
            import objc  # type: ignore[import-untyped]
            from AppKit import (  # type: ignore[import-untyped]
                NSScreenSaverWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorIgnoresCycle,
                NSWindowCollectionBehaviorStationary,
            )

            ns_view = objc.objc_object(c_void_p=window_id)
            ns_window = ns_view.window()
            if ns_window is None:
                return
            ns_window.setLevel_(NSScreenSaverWindowLevel)
            ns_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorIgnoresCycle
            )
            ns_window.orderFrontRegardless()
        except BaseException:  # noqa: BLE001
            return

    def paste_keystroke(self, *, submit: bool = False) -> None:
        from pynput import keyboard

        controller = keyboard.Controller()
        time.sleep(0.15)
        with controller.pressed(keyboard.Key.cmd):
            controller.press("v")
            controller.release("v")
        if submit:
            time.sleep(0.1)
            controller.press(keyboard.Key.enter)
            controller.release(keyboard.Key.enter)


# ---------------------------------------------------------------------------
# Windows implementation
# ---------------------------------------------------------------------------

class _WindowsServices:
    def get_frontmost_window(self, own_window_id: int = 0) -> FocusInfo | None:
        try:
            import ctypes

            hwnd = int(ctypes.windll.user32.GetForegroundWindow())  # type: ignore[attr-defined]
            if hwnd == own_window_id or hwnd == 0:
                return None
            return FocusInfo(hwnd=hwnd)
        except BaseException:  # noqa: BLE001
            return None

    def restore_focus(self, target: FocusInfo | None) -> None:
        if target is None or not target.hwnd:
            return
        try:
            import ctypes
            from ctypes import wintypes

            ctypes.windll.user32.SetForegroundWindow(wintypes.HWND(target.hwnd))  # type: ignore[attr-defined]
        except BaseException:  # noqa: BLE001
            return

    def enforce_topmost(self, window_id: int) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = wintypes.HWND(window_id)
            hwnd_topmost = wintypes.HWND(-1)
            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_noactivate = 0x0010
            ctypes.windll.user32.SetWindowPos(  # type: ignore[attr-defined]
                hwnd, hwnd_topmost, 0, 0, 0, 0,
                swp_nomove | swp_nosize | swp_noactivate,
            )
        except BaseException:  # noqa: BLE001
            return

    def paste_keystroke(self, *, submit: bool = False) -> None:
        from pynput import keyboard

        controller = keyboard.Controller()
        time.sleep(0.15)
        with controller.pressed(keyboard.Key.ctrl):
            controller.press("v")
            controller.release("v")
        if submit:
            time.sleep(0.1)
            controller.press(keyboard.Key.enter)
            controller.release(keyboard.Key.enter)


# ---------------------------------------------------------------------------
# Fallback implementation (Linux / other platforms)
# ---------------------------------------------------------------------------

class _FallbackServices:
    """Best-effort services for Linux and other unsupported platforms."""

    def get_frontmost_window(self, own_window_id: int = 0) -> FocusInfo | None:
        return None

    def restore_focus(self, target: FocusInfo | None) -> None:
        return

    def enforce_topmost(self, window_id: int) -> None:
        return

    def paste_keystroke(self, *, submit: bool = False) -> None:
        from pynput import keyboard

        controller = keyboard.Controller()
        time.sleep(0.15)
        with controller.pressed(keyboard.Key.ctrl):
            controller.press("v")
            controller.release("v")
        if submit:
            time.sleep(0.1)
            controller.press(keyboard.Key.enter)
            controller.release(keyboard.Key.enter)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_platform_services() -> PlatformServices:
    """Return the platform services singleton for the running OS."""
    if sys.platform == "darwin":
        return _MacOSServices()
    if sys.platform == "win32":
        return _WindowsServices()
    return _FallbackServices()
