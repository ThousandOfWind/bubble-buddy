"""Pluggable per-app context extractors.

A *context plugin* looks at the user's focused surface (app, window title,
focused-control text and its raw UI ancestry) and returns extra textual context
to feed the polisher — for example the recent Copilot CLI conversation
transcript, so the model keeps translating domain terms the same way the ongoing
session does. Each plugin *interprets* that native input itself (it resolves any
app-specific concepts, such as a Copilot CLI session, on its own).

Plugins live in one of two places:

* the **official catalog** — :mod:`copilot_voice_shell.plugins_catalog`, one
  self-contained module per plugin, each documented by its module docstring.
  Users browse and install these with ``copilot-voice-shell plugins`` (they are
  enabled via the ``enabled_plugins`` list in ``config.json``); catalog plugins
  marked ``DEFAULT_ENABLED = True`` are active out of the box.
* the **user directory** — ``~/.copilot-voice-shell/plugins`` (or the directory
  named by ``$CVS_PLUGINS_DIR``); any ``*.py`` file there is loaded and active
  automatically, so power users can drop in their own without touching config.

A plugin is a tiny object::

    class MyPlugin:
        name = "my_app"

        def matches(self, ctx: PluginInput) -> bool:
            return "myapp" in ctx.exe_path.lower()

        def extract(self, ctx: PluginInput) -> PluginResult | None:
            return PluginResult(self.name, "My App", "...context...")

A module exposes a plugin via a module-level ``PLUGIN`` (an instance),
``PLUGINS`` (a list), or a ``register()`` callable returning instances.

Everything is best-effort and heavily guarded: a broken or slow plugin must
never break dictation, so every plugin call is wrapped and failures are ignored.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

_CATALOG_PACKAGE = "copilot_voice_shell.plugins_catalog"


@dataclass
class PluginInput:
    """The raw, native description of the focused surface handed to every plugin.

    It intentionally carries only *native* facts (app, window title, focused-control
    text and its raw ancestry) — never app-specific concepts the host resolved on a
    plugin's behalf. A plugin *interprets* this itself: it decides whether it applies
    and what to extract. (For example, the Copilot CLI plugin resolves the CLI
    session and confirms the focused pane from ``title``/``ancestry`` on its own,
    rather than receiving a pre-baked ``session_id``.)"""

    system: str = ""
    app_name: str = ""
    exe_path: str = ""
    hwnd: int = 0
    title: str = ""
    sub_kind: str = ""  # terminal | editor | chat | browser | document | ""
    content: str = ""  # best-effort focused text already gathered by enrich()
    ancestry: tuple = ()  # raw focused-control ancestry: (ControlType, Name, ClassName) tuples


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


@dataclass
class PluginSpec:
    """Descriptor for an available plugin, used by the CLI/UI to browse them."""

    name: str  # plugin id (the key used to install/uninstall)
    summary: str  # first line of the module docstring
    description: str  # full module docstring
    default_enabled: bool  # active out of the box (catalog only)
    source: str  # "catalog" | "user"
    installed: bool = False  # currently active
    instance: object = None


def _is_plugin(obj: object) -> bool:
    return (
        obj is not None
        and isinstance(getattr(obj, "name", None), str)
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


def _docstrings(module: object) -> tuple[str, str]:
    """Return (summary, description) from a plugin module's docstring."""
    doc = (getattr(module, "__doc__", "") or "").strip()
    if not doc:
        return "", ""
    summary = doc.splitlines()[0].strip()
    return summary, doc


# --------------------------------------------------------------------------- #
# Catalog discovery (official, bundled plugins)
# --------------------------------------------------------------------------- #

_catalog_cache: "list[PluginSpec] | None" = None


def discover_catalog(refresh: bool = False) -> list[PluginSpec]:
    """Import every module in the official catalog package and return one
    :class:`PluginSpec` per plugin instance it contributes. Cached; pass
    ``refresh=True`` to re-import. Fully guarded — a broken catalog module is
    skipped rather than breaking discovery."""
    global _catalog_cache
    if _catalog_cache is not None and not refresh:
        return list(_catalog_cache)
    specs: list[PluginSpec] = []
    try:
        pkg = importlib.import_module(_CATALOG_PACKAGE)
        for info in pkgutil.iter_modules(pkg.__path__):
            if info.name.startswith("_"):
                continue
            mod_name = f"{_CATALOG_PACKAGE}.{info.name}"
            try:
                module = importlib.import_module(mod_name)
                if refresh:
                    module = importlib.reload(module)
            except BaseException:
                continue
            summary, description = _docstrings(module)
            default_enabled = bool(getattr(module, "DEFAULT_ENABLED", False))
            for plugin in _plugins_from_module(module):
                specs.append(
                    PluginSpec(
                        name=plugin.name,
                        summary=summary,
                        description=description,
                        default_enabled=default_enabled,
                        source="catalog",
                        instance=plugin,
                    )
                )
    except BaseException:
        return list(_catalog_cache or [])
    _catalog_cache = specs
    return list(specs)


# --------------------------------------------------------------------------- #
# User-directory discovery (drop-in plugins)
# --------------------------------------------------------------------------- #

_user_cache: "list[PluginSpec] | None" = None


def plugins_dir() -> Path:
    """Directory scanned for user-authored drop-in plugins (``$CVS_PLUGINS_DIR``
    or ``~/.copilot-voice-shell/plugins``)."""
    env = os.environ.get("CVS_PLUGINS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".copilot-voice-shell" / "plugins"


def discover_user(refresh: bool = False) -> list[PluginSpec]:
    global _user_cache
    if _user_cache is not None and not refresh:
        return list(_user_cache)
    specs: list[PluginSpec] = []
    try:
        directory = plugins_dir()
        if directory.is_dir():
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
                summary, description = _docstrings(module)
                for plugin in _plugins_from_module(module):
                    specs.append(
                        PluginSpec(
                            name=plugin.name,
                            summary=summary,
                            description=description,
                            default_enabled=True,  # present in the dir == active
                            source="user",
                            installed=True,
                            instance=plugin,
                        )
                    )
    except BaseException:
        return list(_user_cache or [])
    _user_cache = specs
    return list(specs)


# --------------------------------------------------------------------------- #
# Enable / install state (config ``enabled_plugins``)
# --------------------------------------------------------------------------- #

def _load_config() -> dict:
    try:
        from . import config as _cfg

        cfg = _cfg.load_config()
        return cfg if isinstance(cfg, dict) else {}
    except BaseException:
        return {}


def enabled_names(refresh: bool = False) -> set[str]:
    """Names of catalog plugins that are currently active. Uses the explicit
    ``enabled_plugins`` config list when present, otherwise the catalog's
    ``DEFAULT_ENABLED`` plugins."""
    cfg = _load_config()
    configured = cfg.get("enabled_plugins")
    if isinstance(configured, list):
        return {str(n).strip() for n in configured if str(n).strip()}
    return {s.name for s in discover_catalog(refresh) if s.default_enabled}


def get_plugins(refresh: bool = False) -> list[ContextPlugin]:
    """Return all active plugin instances: enabled catalog plugins plus every
    user-directory plugin (which is active simply by being present)."""
    active = enabled_names(refresh)
    out: list[ContextPlugin] = [
        s.instance
        for s in discover_catalog(refresh)
        if s.name in active and s.instance is not None
    ]
    out.extend(
        s.instance for s in discover_user(refresh) if s.instance is not None
    )
    return out


def list_plugins(refresh: bool = True) -> list[PluginSpec]:
    """Return all known plugins (catalog + user) with an up-to-date ``installed``
    flag, for the CLI/settings UI to display."""
    active = enabled_names(refresh)
    specs: list[PluginSpec] = []
    for spec in discover_catalog(refresh):
        spec.installed = spec.name in active
        specs.append(spec)
    specs.extend(discover_user(refresh))
    return specs


def install_plugin(name: str) -> bool:
    """Enable a catalog plugin by name (persisted to ``enabled_plugins`` in
    config). Returns True on success, False if no such catalog plugin exists."""
    name = (name or "").strip()
    known = {s.name for s in discover_catalog()}
    if name not in known:
        return False
    enabled = enabled_names()
    enabled.add(name)
    _save_enabled(enabled)
    return True


def uninstall_plugin(name: str) -> bool:
    """Disable a catalog plugin by name. Returns True on success, False if no such
    catalog plugin exists."""
    name = (name or "").strip()
    known = {s.name for s in discover_catalog()}
    if name not in known:
        return False
    enabled = enabled_names()
    enabled.discard(name)
    _save_enabled(enabled)
    return True


def _save_enabled(enabled: set[str]) -> None:
    try:
        from . import config as _cfg

        _cfg.save_config({"enabled_plugins": sorted(enabled)})
    except BaseException:
        pass


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #

def extract_all(ctx: PluginInput) -> list[PluginResult]:
    """Run every active plugin against ``ctx`` and collect their results. Each
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
