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


def _startup_command() -> str:
    """The command line that (re)launches the desktop overlay, quoted for use in
    an OS autostart entry. Uses the frozen executable when packaged, else the
    current Python interpreter plus the ``app_launcher.py`` shim."""
    import os

    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" desktop'
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    launcher = os.path.join(repo_root, "packaging", "app_launcher.py")
    return f'"{sys.executable}" "{launcher}" desktop'


def suppress_child_console_windows() -> None:
    """Ensure subprocesses spawned by this GUI app never flash a console window.

    A windowed (``console=False``) app has no console, so any child that is a
    console program — e.g. azure-identity shelling out to ``az.cmd``/``pwsh`` for
    token acquisition, or ollama — pops a transient black window. On Windows we
    patch :class:`subprocess.Popen` to default ``creationflags`` to
    ``CREATE_NO_WINDOW`` when the caller didn't request specific flags. No-op on
    other platforms and idempotent."""
    if sys.platform != "win32":
        return
    import subprocess

    if getattr(subprocess.Popen, "_cvs_no_window_patched", False):
        return
    create_no_window = 0x08000000
    _orig_init = subprocess.Popen.__init__

    def _init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Only supply the flag when the caller left creationflags unset, so we
        # never override an explicit CREATE_NEW_CONSOLE / detached request.
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = create_no_window
        _orig_init(self, *args, **kwargs)

    _init._cvs_no_window_patched = True  # type: ignore[attr-defined]
    subprocess.Popen.__init__ = _init  # type: ignore[method-assign]
    subprocess.Popen._cvs_no_window_patched = True  # type: ignore[attr-defined]


@dataclass(frozen=True)
class FocusInfo:
    """Platform-agnostic snapshot of a focused window / application.

    Fields are populated on a best-effort basis; absent fields default to
    their zero value.

    Attributes:
        name:       Human-readable application name (all platforms).
        bundle_id:  macOS bundle identifier (e.g. ``com.microsoft.VSCode``).
        pid:        Process identifier (macOS/Windows).
        hwnd:       Win32 window handle (Windows).
        exe_path:   Full path to the process executable (Windows).
    """

    name: str = ""
    bundle_id: str = ""
    pid: int = 0
    hwnd: int = 0
    exe_path: str = ""


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

    def set_launch_at_startup(self, enabled: bool) -> bool:
        """Enable or disable launching the app automatically on login.

        Returns ``True`` on success. Implementations should be idempotent and
        must never raise.
        """

    def get_launch_at_startup(self) -> bool:
        """Return whether the app is currently registered to launch on login."""


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
            try:
                if ns_window.respondsToSelector_("setHidesOnDeactivate:"):
                    ns_window.setHidesOnDeactivate_(False)
                if ns_window.respondsToSelector_("setCanHide:"):
                    ns_window.setCanHide_(False)
            except BaseException:  # noqa: BLE001
                pass
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
        import subprocess

        time.sleep(0.15)
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'],
            check=False,
        )
        if submit:
            time.sleep(0.1)
            subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to key code 36'],
                check=False,
            )

    _LAUNCH_AGENT_LABEL = "com.bubblebuddy.overlay"

    def _launch_agent_path(self) -> str:
        import os

        return os.path.expanduser(
            f"~/Library/LaunchAgents/{self._LAUNCH_AGENT_LABEL}.plist"
        )

    def set_launch_at_startup(self, enabled: bool) -> bool:
        import os

        path = self._launch_agent_path()
        try:
            if not enabled:
                if os.path.exists(path):
                    os.remove(path)
                return True
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if getattr(sys, "frozen", False):
                args = [sys.executable, "desktop"]
            else:
                repo = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                args = [sys.executable, os.path.join(repo, "packaging", "app_launcher.py"), "desktop"]
            arg_xml = "".join(f"        <string>{a}</string>\n" for a in args)
            plist = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n<dict>\n'
                f"    <key>Label</key>\n    <string>{self._LAUNCH_AGENT_LABEL}</string>\n"
                f"    <key>ProgramArguments</key>\n    <array>\n{arg_xml}    </array>\n"
                "    <key>RunAtLoad</key>\n    <true/>\n"
                "</dict>\n</plist>\n"
            )
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(plist)
            return True
        except OSError:
            return False

    def get_launch_at_startup(self) -> bool:
        import os

        return os.path.exists(self._launch_agent_path())


# ---------------------------------------------------------------------------
# Windows implementation
# ---------------------------------------------------------------------------

class _WindowsServices:
    @staticmethod
    def _friendly_app_name(exe_path: str) -> str:
        """A human-readable app name for *exe_path*: prefer the executable's
        FileDescription version string (e.g. 'Visual Studio Code', 'Microsoft
        Teams'), falling back to the file name without its extension."""
        import os

        base = os.path.splitext(os.path.basename(exe_path))[0] if exe_path else ""
        if not exe_path:
            return base
        try:
            import ctypes
            from ctypes import wintypes

            ver = ctypes.windll.version
            size = ver.GetFileVersionInfoSizeW(exe_path, None)
            if not size:
                return base
            buf = ctypes.create_string_buffer(size)
            if not ver.GetFileVersionInfoW(exe_path, 0, size, buf):
                return base
            # Query the translation table, then read StringFileInfo\<lang>\FileDescription.
            lp = ctypes.c_void_p()
            length = wintypes.UINT()
            if not ver.VerQueryValueW(
                buf, "\\VarFileInfo\\Translation", ctypes.byref(lp), ctypes.byref(length)
            ) or not length.value:
                return base
            lang, codepage = ctypes.cast(
                lp, ctypes.POINTER(wintypes.WORD * 2)
            ).contents
            sub = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription"
            if ver.VerQueryValueW(buf, sub, ctypes.byref(lp), ctypes.byref(length)) and length.value:
                desc = ctypes.wstring_at(lp.value, length.value - 1).strip()
                if desc:
                    return desc
        except BaseException:  # noqa: BLE001
            pass
        return base

    def get_frontmost_window(self, own_window_id: int = 0) -> FocusInfo | None:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = int(user32.GetForegroundWindow())
            if hwnd == own_window_id or hwnd == 0:
                return None

            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe_path = ""
            if pid.value:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
                )
                if handle:
                    try:
                        buf_len = wintypes.DWORD(32768)
                        buf = ctypes.create_unicode_buffer(buf_len.value)
                        if kernel32.QueryFullProcessImageNameW(
                            handle, 0, buf, ctypes.byref(buf_len)
                        ):
                            exe_path = buf.value
                    finally:
                        kernel32.CloseHandle(handle)

            name = self._friendly_app_name(exe_path)
            return FocusInfo(name=name, hwnd=hwnd, pid=pid.value, exe_path=exe_path)
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

    _RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _RUN_VALUE = "BubbleBuddy"

    def set_launch_at_startup(self, enabled: bool) -> bool:
        try:
            import winreg

            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY)
            try:
                if enabled:
                    winreg.SetValueEx(
                        key, self._RUN_VALUE, 0, winreg.REG_SZ, _startup_command()
                    )
                else:
                    try:
                        winreg.DeleteValue(key, self._RUN_VALUE)
                    except FileNotFoundError:
                        pass
                return True
            finally:
                winreg.CloseKey(key)
        except OSError:
            return False

    def get_launch_at_startup(self) -> bool:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY) as key:
                winreg.QueryValueEx(key, self._RUN_VALUE)
                return True
        except OSError:
            return False

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

    def set_launch_at_startup(self, enabled: bool) -> bool:
        return False

    def get_launch_at_startup(self) -> bool:
        return False


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
