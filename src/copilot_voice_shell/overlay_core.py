"""Platform-independent helpers shared by the native macOS overlay.

These helpers deliberately avoid importing AppKit/objc so they can be imported
(and unit-tested) on any platform, including Windows CI. The AppKit-dependent
overlay lives in :mod:`copilot_voice_shell.overlay`, which re-exports these
names for backwards compatibility.
"""

from __future__ import annotations

from pynput import keyboard

from .cli import normalize_hotkey


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


def make_hotkey_listener(hotkey: str, controller):
    def _on_hotkey() -> None:
        controller.performSelectorOnMainThread_withObject_waitUntilDone_(
            "toggleRecording:",
            None,
            False,
        )

    return keyboard.GlobalHotKeys({normalize_hotkey(hotkey): _on_hotkey})
