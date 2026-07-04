"""Best-effort deep inspection of the user's currently-focused window.

The plain foreground-window probe (in qt_overlay) only yields the outermost app
(exe name). This module goes one level deeper — the window *title* and, on
Windows via UI Automation, the *focused control* and its text — so the polisher
can adapt to what the user is actually doing (VS Code editor vs integrated
terminal, which Teams conversation, which browser page).

Everything here is best-effort and heavily guarded: Electron apps (VS Code,
Teams) expose only a partial accessibility tree, and terminal buffers are
readable only when the app's screen-reader mode is on. Any failure degrades
gracefully to "title only" (or nothing) rather than raising.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Optional

# Cap injected content so a huge editor/terminal buffer can't blow up the prompt.
_MAX_CONTENT = 1200
_MAX_TREE_NODES = 400


@dataclass
class FocusInfo:
    """What we could learn about the focused window beyond its exe name."""

    title: str = ""
    sub_kind: str = ""  # "terminal" | "editor" | "chat" | "browser" | "document" | ""
    content: str = ""  # best-effort text the user is focused on
    session: Optional["SessionInfo"] = None  # resolved Copilot CLI session (terminals)
    copilot_cli: bool = False  # confident: focused pane is a Copilot CLI terminal

    @property
    def is_empty(self) -> bool:
        return not (
            self.title or self.sub_kind or self.content or self.session or self.copilot_cli
        )


@dataclass
class SessionInfo:
    """The Copilot CLI session behind the focused terminal (best-effort)."""

    id: str = ""
    summary: str = ""
    repository: str = ""
    branch: str = ""
    cwd: str = ""
    exact: bool = False


def window_title(hwnd: int) -> str:
    """Cheap window-title read (safe to call frequently). Windows only."""
    if platform.system() != "Windows" or not hwnd:
        return ""
    try:
        import ctypes

        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return (buf.value or "").strip()
    except BaseException:
        return ""


def enrich(system: str, hwnd: int, exe_path: str, app_name: str) -> FocusInfo:
    """Deep, best-effort inspection. Slower (UI Automation) — call at record time,
    not from the fast focus poller."""
    if system == "Windows":
        return _enrich_windows(hwnd, exe_path, app_name)
    if system == "Darwin":
        return _enrich_macos(app_name)
    return FocusInfo()


# --------------------------------------------------------------------------- #
# Windows (UI Automation via `uiautomation`)
# --------------------------------------------------------------------------- #

def _enrich_windows(hwnd: int, exe_path: str, app_name: str) -> FocusInfo:
    info = FocusInfo(title=window_title(hwnd))
    exe = (exe_path or "").lower()
    try:
        import uiautomation as auto
    except BaseException:
        return info

    try:
        focused = auto.GetFocusedControl()
    except BaseException:
        focused = None
    if focused is None:
        info.sub_kind = _sub_kind_from_title(info.title, exe)
        if _looks_like_vscode(exe, info.title):
            info.session = _resolve_session(info.title, info.title)
        info.copilot_cli = _detect_copilot_cli(
            info.title, [], info.session.summary if info.session else "", exe
        )
        return info

    # Collect the focused element + a few ancestors so we can classify by the
    # monaco-*/xterm class names VS Code/Electron apps expose.
    chain: list[tuple[str, str, str]] = []
    node = focused
    for _ in range(7):
        if node is None:
            break
        try:
            chain.append((node.ControlTypeName or "", node.Name or "", node.ClassName or ""))
        except BaseException:
            break
        try:
            node = node.GetParentControl()
        except BaseException:
            break

    info.sub_kind = _classify(chain, exe) or _sub_kind_from_title(info.title, exe)

    # Read the most useful text we can reach.
    content = ""
    if info.sub_kind == "terminal":
        content = _terminal_text(focused) or _read_text(focused)
    else:
        content = _read_text(focused)
        if not content:
            content = _deep_text(focused, depth=3)
    info.content = _clip(content)

    # Bridge a focused VS Code window to its Copilot CLI session. We attempt this
    # for any VS Code (or fork) window — NOT only when we managed to classify the
    # focus as a terminal — because the xterm canvas often defeats classification.
    # The session summary (== terminal tab title) shows up in the ancestry
    # accessible names, so pass those plus the window title as the match blob.
    if _looks_like_vscode(exe, info.title):
        blob = "\n".join(name for _t, name, _c in chain if name)
        info.session = _resolve_session(info.title, f"{info.title}\n{blob}")

    # Confident, pane-level test that the FOCUSED surface is the Copilot CLI
    # terminal (not the editor beside it, nor a plain shell). This drives the
    # "copilot" polish style; a merely-resolvable window session is NOT enough.
    info.copilot_cli = _detect_copilot_cli(
        info.title, chain, info.session.summary if info.session else "", exe
    )
    return info


def _looks_like_vscode(exe: str, title: str) -> bool:
    exe = (exe or "").lower()
    t = (title or "").lower()
    if any(k in exe for k in ("code", "vscodium", "cursor")):
        return True
    return any(
        k in t for k in ("visual studio code", "code - oss", "vscodium", "cursor")
    )


def _resolve_session(window_title: str, blob: str) -> Optional[SessionInfo]:
    """Best-effort: map the focused VS Code terminal to its Copilot CLI session."""
    try:
        from . import copilot_session

        match = copilot_session.resolve_session(window_title, blob)
    except BaseException:
        return None
    if match is None or match.is_empty:
        return None
    return SessionInfo(
        id=match.id,
        summary=match.summary,
        repository=match.repository,
        branch=match.branch,
        cwd=match.cwd,
        exact=match.exact,
    )


def _classify(chain: list[tuple[str, str, str]], exe: str) -> str:
    blob = " ".join(f"{n} {c}".lower() for _t, n, c in chain)
    if "xterm" in blob or "terminal" in blob:
        return "terminal"
    if "monaco-editor" in blob or "editor-instance" in blob:
        return "editor"
    if "teams" in exe or "teams" in blob:
        if any(k in blob for k in ("message", "compose", "chat", "conversation")):
            return "chat"
        return "chat"
    if any(b in exe for b in ("chrome", "msedge", "firefox", "opera", "brave", "vivaldi")):
        return "browser"
    # Editable text control ⇒ a document/compose box.
    for t, _n, _c in chain:
        if t in ("EditControl", "DocumentControl"):
            return "document"
    return ""


# --------------------------------------------------------------------------- #
# Copilot CLI terminal detection
# --------------------------------------------------------------------------- #

# Marker substrings (control name/class) and exe names that identify a terminal
# surface. Chromium/Electron UIA rarely exposes DOM class names ("xterm",
# "monaco-editor"), so we also match native terminal hosts (Windows Terminal,
# conhost, ConEmu, …) whose focused control DOES expose a recognisable class.
_TERMINAL_MARKERS = (
    "xterm", "terminal", "termcontrol", "cascadia",
    "consolewindowclass", "conemu", "mintty", "vt100",
)
_TERMINAL_EXES = (
    "windowsterminal", "wt.exe", "conhost", "cmd.exe", "powershell", "pwsh",
    "alacritty", "wezterm", "kitty", "mintty", "conemu", "putty",
)


def _focus_is_terminal(chain: list[tuple[str, str, str]], exe: str) -> bool:
    """True when the focused surface looks like a terminal (native host exe or a
    recognisable terminal control class in the focused ancestry)."""
    exe_l = (exe or "").lower()
    if any(k in exe_l for k in _TERMINAL_EXES):
        return True
    blob = " ".join(f"{n} {c}" for _t, n, c in chain).lower()
    return any(k in blob for k in _TERMINAL_MARKERS)


def _detect_copilot_cli(
    title: str,
    chain: list[tuple[str, str, str]],
    session_summary: str,
    exe: str,
) -> bool:
    """Confident test that the *focused pane* is a Copilot CLI terminal (not the
    editor next to it, nor a plain shell).

    Two positive signals, both pane-level (a resolvable session for the whole VS
    Code window is NOT enough — the user may be in the editor):

    1. VS Code integrated terminal: the terminal *tab* accessible name (== the
       session ``summary``) appears in the FOCUSED control's ancestry. That tab is
       in the focused ancestry only when the terminal pane — not the editor — has
       focus.
    2. A dedicated terminal (Windows Terminal / conhost / …) whose window or tab
       title the CLI sets to "GitHub Copilot".
    """
    ancestry = "\n".join(n for _t, n, _c in chain if n)
    summary = (session_summary or "").strip()
    if len(summary) >= 4 and summary.lower() in ancestry.lower():
        return True
    if _focus_is_terminal(chain, exe) and "github copilot" in f"{title}\n{ancestry}".lower():
        return True
    return False


def _sub_kind_from_title(title: str, exe: str) -> str:
    t = (title or "").lower()
    if "teams" in exe:
        return "chat"
    if any(b in exe for b in ("chrome", "msedge", "firefox", "opera", "brave", "vivaldi")):
        return "browser"
    if "visual studio code" in t or "code" in exe:
        return "editor"
    return ""


def _read_text(control) -> str:
    """Read text from a control via Value/Text/LegacyIAccessible patterns."""
    if control is None:
        return ""
    getter = getattr(control, "GetValuePattern", None)
    if getter is not None:
        try:
            vp = getter()
            val = getattr(vp, "Value", "") if vp else ""
            if val and val.strip():
                return val
        except BaseException:
            pass
    getter = getattr(control, "GetTextPattern", None)
    if getter is not None:
        try:
            tp = getter()
            if tp is not None:
                txt = tp.DocumentRange.GetText(_MAX_CONTENT)
                if txt and txt.strip():
                    return txt
        except BaseException:
            pass
    getter = getattr(control, "GetLegacyIAccessiblePattern", None)
    if getter is not None:
        try:
            lp = getter()
            val = getattr(lp, "Value", "") if lp else ""
            if val and val.strip():
                return val
        except BaseException:
            pass
    return ""


def _deep_text(control, depth: int) -> str:
    """Collect text from descendant Text/Edit/Document controls, breadth-limited."""
    out: list[str] = []
    seen = 0

    def walk(node, d):
        nonlocal seen
        if node is None or d < 0 or seen >= _MAX_TREE_NODES:
            return
        try:
            children = node.GetChildren()
        except BaseException:
            return
        for child in children:
            if seen >= _MAX_TREE_NODES:
                return
            seen += 1
            try:
                ctype = child.ControlTypeName or ""
            except BaseException:
                continue
            if ctype in ("TextControl", "EditControl", "DocumentControl"):
                txt = _read_text(child) or _safe_name(child)
                if txt and txt.strip():
                    out.append(txt.strip())
            walk(child, d - 1)
            if sum(len(x) for x in out) >= _MAX_CONTENT:
                return

    walk(control, depth)
    return "\n".join(out)


def _terminal_text(focused) -> str:
    """Try to read a VS Code / Windows terminal buffer. The buffer text is exposed
    only when the app's accessibility (screen-reader) mode is on; otherwise this
    returns whatever visible text we can reach. Best-effort."""
    # Climb to a container, then gather descendant text (the accessibility buffer
    # is a Text/Document control near the terminal region).
    node = focused
    for _ in range(4):
        if node is None:
            break
        txt = _deep_text(node, depth=3)
        if txt and len(txt.strip()) > 20:
            return txt
        try:
            node = node.GetParentControl()
        except BaseException:
            break
    return ""


def _safe_name(control) -> str:
    try:
        return control.Name or ""
    except BaseException:
        return ""


def _clip(text: str) -> str:
    if not text:
        return ""
    text = " ".join(text.split()) if "\n" not in text else text
    text = text.strip()
    return text[:_MAX_CONTENT]


# --------------------------------------------------------------------------- #
# macOS (best-effort; the app name is already known by the caller)
# --------------------------------------------------------------------------- #

def _enrich_macos(app_name: str) -> FocusInfo:
    """Best-effort focused-window title + text via the Accessibility (AX) API.
    Requires the app to be granted Accessibility permission; degrades to empty."""
    info = FocusInfo()
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateSystemWide,
            kAXFocusedApplicationAttribute,
            kAXFocusedUIElementAttribute,
            kAXTitleAttribute,
            kAXValueAttribute,
        )

        system = AXUIElementCreateSystemWide()
        err, focused_app = AXUIElementCopyAttributeValue(
            system, kAXFocusedApplicationAttribute, None
        )
        if err or focused_app is None:
            return info
        err, title = AXUIElementCopyAttributeValue(focused_app, kAXTitleAttribute, None)
        if not err and title:
            info.title = str(title)
        err, elem = AXUIElementCopyAttributeValue(
            system, kAXFocusedUIElementAttribute, None
        )
        if not err and elem is not None:
            err, value = AXUIElementCopyAttributeValue(elem, kAXValueAttribute, None)
            if not err and value:
                info.content = _clip(str(value))
    except BaseException:
        return info

    # VS Code (and forks) share the ~/.copilot store on macOS; bridge a focused
    # terminal to its Copilot CLI session using the title + focused text as blob.
    if any(k in (app_name or "").lower() for k in ("code", "vscodium", "cursor")):
        blob = f"{info.title}\n{info.content}"
        session = _resolve_session(info.title, blob)
        if session is not None:
            info.session = session
            info.sub_kind = info.sub_kind or "terminal"
            # No UIA control tree on macOS; feed the title/content as a pseudo-chain
            # so the same confident detector (session summary present in the focused
            # blob) decides whether this really is the Copilot CLI pane.
            info.copilot_cli = _detect_copilot_cli(
                info.title,
                [("", info.title, ""), ("", info.content, "")],
                session.summary,
                app_name,
            )
    return info
