"""Pluggable per-app context extractors.

A *context plugin* looks at the user's focused surface (app, window title,
focused-control text, resolved Copilot CLI session, ...) and returns extra
textual context to feed the polisher — for example the recent Copilot CLI
conversation transcript, so the model keeps translating domain terms the same
way the ongoing session does.

Plugins are deliberately tiny so users can add their own for niche apps::

    class MyPlugin:
        name = "my_plugin"

        def matches(self, ctx: PluginInput) -> bool:
            return "myapp" in ctx.exe_path.lower()

        def extract(self, ctx: PluginInput) -> PluginResult | None:
            return PluginResult(self.name, "My App", "...context...")

Drop a ``*.py`` file that exposes a module-level ``PLUGIN`` (an instance),
``PLUGINS`` (a list) or a ``register()`` callable returning instances into
``~/.copilot-voice-shell/plugins`` (or the directory named by ``$CVS_PLUGINS_DIR``)
and it is discovered automatically on the next run. Built-in plugins ship in
code. A plugin can be turned off by adding its ``name`` to the
``disabled_context_plugins`` list in ``config.json``.

Everything is best-effort and heavily guarded: a broken or slow plugin must
never break dictation, so every plugin call is wrapped and failures are ignored.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class PluginInput:
    """Everything a context plugin may inspect about the focused surface."""

    system: str = ""
    app_name: str = ""
    exe_path: str = ""
    hwnd: int = 0
    title: str = ""
    sub_kind: str = ""  # terminal | editor | chat | browser | document | ""
    content: str = ""  # best-effort focused text already gathered by enrich()
    copilot_cli: bool = False  # focused pane is confidently a Copilot CLI terminal
    session_id: str = ""  # resolved Copilot CLI session id (if any)
    session_summary: str = ""


@dataclass
class PluginResult:
    """A block of extra context contributed by a plugin."""

    name: str  # the plugin's id
    label: str  # short human label, e.g. "Copilot 会话记录"
    text: str  # the context text injected into the polish prompt

    @property
    def is_empty(self) -> bool:
        return not (self.text or "").strip()


@runtime_checkable
class ContextPlugin(Protocol):
    """The tiny contract a context plugin must satisfy."""

    name: str

    def matches(self, ctx: PluginInput) -> bool:
        """Return True if this plugin applies to the focused surface."""
        ...

    def extract(self, ctx: PluginInput) -> "PluginResult | None":
        """Return extra context for the focused surface, or None."""
        ...


# --------------------------------------------------------------------------- #
# Built-in: Copilot CLI conversation transcript
# --------------------------------------------------------------------------- #

def _one_line(text: str, limit: int = 200) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


class CopilotCliPlugin:
    """Inject the recent Copilot CLI conversation transcript when the focused pane
    is a Copilot CLI terminal, so the polisher stays consistent with the terms and
    topic of the ongoing session (better than translating each utterance blind)."""

    name = "copilot_cli"
    label = "Copilot 会话记录"

    def __init__(self, max_turns: int = 4, max_chars: int = 900) -> None:
        self.max_turns = max_turns
        self.max_chars = max_chars

    def matches(self, ctx: PluginInput) -> bool:
        return bool(ctx.copilot_cli and ctx.session_id)

    def extract(self, ctx: PluginInput) -> "PluginResult | None":
        from . import copilot_session

        turns = copilot_session.recent_turns(ctx.session_id, limit=self.max_turns)
        if not turns:
            return None
        lines: list[str] = []
        for turn in turns:
            user = _one_line(turn.user_message)
            reply = _one_line(turn.assistant_response)
            if user:
                lines.append(f"我：{user}")
            if reply:
                lines.append(f"Copilot：{reply}")
        text = "\n".join(lines).strip()
        if not text:
            return None
        if len(text) > self.max_chars:
            # Keep the most recent context (the tail) when we have to truncate.
            text = "…" + text[-self.max_chars:]
        return PluginResult(name=self.name, label=self.label, text=text)


# Built-in plugins ship in code and are always available (unless disabled).
_BUILTIN: list[ContextPlugin] = [CopilotCliPlugin()]

_user_cache: "list[ContextPlugin] | None" = None


# --------------------------------------------------------------------------- #
# Discovery of user plugins
# --------------------------------------------------------------------------- #

def plugins_dir() -> Path:
    """Directory scanned for user-authored plugins (``$CVS_PLUGINS_DIR`` or
    ``~/.copilot-voice-shell/plugins``)."""
    env = os.environ.get("CVS_PLUGINS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".copilot-voice-shell" / "plugins"


def _is_plugin(obj: object) -> bool:
    return (
        obj is not None
        and hasattr(obj, "name")
        and callable(getattr(obj, "matches", None))
        and callable(getattr(obj, "extract", None))
    )


def _plugins_from_module(module: object) -> list[ContextPlugin]:
    found: list[object] = []
    register = getattr(module, "register", None)
    if callable(register):
        try:
            produced = register()
        except BaseException:
            produced = None
        if isinstance(produced, (list, tuple)):
            found.extend(produced)
        elif produced is not None:
            found.append(produced)
    for attr in ("PLUGINS", "PLUGIN"):
        obj = getattr(module, attr, None)
        if obj is None:
            continue
        if isinstance(obj, (list, tuple)):
            found.extend(obj)
        else:
            found.append(obj)
    return [p for p in found if _is_plugin(p)]


def _load_user_plugins() -> list[ContextPlugin]:
    out: list[ContextPlugin] = []
    try:
        directory = plugins_dir()
        if not directory.is_dir():
            return out
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"cvs_plugin_{path.stem}", path
                )
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore[union-attr]
            except BaseException:
                continue  # a broken plugin file must not break the others
            out.extend(_plugins_from_module(module))
    except BaseException:
        return out
    return out


def _disabled_names() -> set[str]:
    try:
        from . import config as _cfg

        cfg = _cfg.load_config()
        names = cfg.get("disabled_context_plugins") or []
        return {str(n).strip() for n in names if str(n).strip()}
    except BaseException:
        return set()


def get_plugins(refresh: bool = False) -> list[ContextPlugin]:
    """Return all active plugins (built-in + user), honouring the
    ``disabled_context_plugins`` config list. User plugins are discovered once and
    cached; pass ``refresh=True`` to rescan the plugins directory."""
    global _user_cache
    if _user_cache is None or refresh:
        _user_cache = _load_user_plugins()
    disabled = _disabled_names()
    return [
        p
        for p in (list(_BUILTIN) + _user_cache)
        if getattr(p, "name", "") not in disabled
    ]


def extract_all(ctx: PluginInput) -> list[PluginResult]:
    """Run every applicable plugin against ``ctx`` and collect their results. Each
    plugin is fully guarded so one misbehaving plugin can never break dictation."""
    results: list[PluginResult] = []
    for plugin in get_plugins():
        try:
            if not plugin.matches(ctx):
                continue
            result = plugin.extract(ctx)
        except BaseException:
            continue
        if result is not None and not result.is_empty:
            results.append(result)
    return results
