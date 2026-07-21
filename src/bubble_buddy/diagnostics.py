"""Always-on diagnostics: a persistent log file plus a copyable snapshot.

The packaged Windows/macOS app is a *windowed* build (``console=False`` in the
PyInstaller spec), so every ``print(...)`` and uncaught traceback is written to a
stdout/stderr that goes nowhere. That makes field problems — most notably "the
F9 hotkey does nothing after installing the package" — impossible to diagnose,
because the very line that would explain it (``[hotkey] failed to start
listener: ...``) is discarded.

This module fixes that by installing an always-on rotating log file under the
user data dir, teeing ``sys.stdout``/``sys.stderr`` into it (so existing prints
are captured verbatim), and routing uncaught exceptions there too. It also
exposes :func:`snapshot` which gathers environment/version/config info plus the
tail of the log into a single copyable block that a user can paste into the
Bubble Buddy support skill for debugging.

Keep this module import-light (no Qt) so it can be set up before the GUI loads.
"""
from __future__ import annotations

import logging
import os
import platform
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "Bubble Buddy"

_LOGGER_NAME = "bubble_buddy"
_configured = False
_log_path: Path | None = None


def data_dir() -> Path:
    """Return the per-user data directory (``~/.bubble-buddy``)."""
    override = os.environ.get("BUBBLE_BUDDY_CONFIG")
    if override:
        try:
            parent = Path(override).expanduser().resolve().parent
            if parent.name:
                return parent
        except Exception:  # noqa: BLE001
            pass
    return Path.home() / ".bubble-buddy"


def log_dir() -> Path:
    """Return the directory holding diagnostic logs, creating it if needed."""
    d = data_dir() / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return d


def log_path() -> Path:
    """Return the path to the main rotating log file."""
    return _log_path or (log_dir() / "bubble-buddy.log")


class _TeeStream:
    """Wrap an original stream so writes also go to the log, preserving the
    existing on-console behaviour when a console *is* present (source runs)."""

    def __init__(self, original, logger: logging.Logger, level: int) -> None:
        self._original = original
        self._logger = logger
        self._level = level
        self._buffer = ""

    def write(self, text: str) -> int:
        try:
            if self._original is not None:
                self._original.write(text)
        except Exception:  # noqa: BLE001
            pass
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                try:
                    self._logger.log(self._level, line)
                except Exception:  # noqa: BLE001
                    pass
        return len(text)

    def flush(self) -> None:
        try:
            if self._original is not None:
                self._original.flush()
        except Exception:  # noqa: BLE001
            pass

    def isatty(self) -> bool:
        try:
            return bool(self._original is not None and self._original.isatty())
        except Exception:  # noqa: BLE001
            return False

    def __getattr__(self, name):  # delegate everything else to the real stream
        return getattr(self._original, name)


def get_logger() -> logging.Logger:
    """Return the shared application logger (does not force setup)."""
    return logging.getLogger(_LOGGER_NAME)


def setup_logging() -> Path:
    """Install the always-on file log + stdout/stderr tee + excepthook.

    Idempotent: safe to call more than once. Returns the log file path.
    """
    global _configured, _log_path
    if _configured:
        return log_path()

    path = log_dir() / "bubble-buddy.log"
    _log_path = path

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        handler = RotatingFileHandler(
            path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
    except Exception:  # noqa: BLE001
        # If the log file cannot be opened we still install the tee/excepthook so
        # nothing crashes; logging just no-ops.
        pass

    # Tee stdout/stderr so the app's many ``print(..., flush=True)`` diagnostics
    # (e.g. "[hotkey] triggered", "[hotkey] failed to start listener: ...") land
    # in the log even in the windowed build where the console is absent.
    try:
        sys.stdout = _TeeStream(sys.stdout, logger, logging.INFO)
        sys.stderr = _TeeStream(sys.stderr, logger, logging.ERROR)
    except Exception:  # noqa: BLE001
        pass

    _install_excepthook(logger)

    _configured = True
    logger.info("=== %s session start ===", APP_NAME)
    for line in _system_lines():
        logger.info(line)
    return path


def _install_excepthook(logger: logging.Logger) -> None:
    prev = sys.excepthook

    def _hook(exc_type, exc, tb) -> None:
        try:
            logger.error("Uncaught exception", exc_info=(exc_type, exc, tb))
        except Exception:  # noqa: BLE001
            pass
        try:
            prev(exc_type, exc, tb)
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = _hook


def log(msg: str) -> None:
    """Convenience wrapper used by callers that prefer not to hold a logger."""
    try:
        get_logger().info(msg)
    except Exception:  # noqa: BLE001
        pass


def _system_lines() -> list[str]:
    frozen = bool(getattr(sys, "frozen", False))
    lines = [
        f"version: {_app_version()}",
        f"frozen: {frozen}  executable: {sys.executable}",
        f"platform: {platform.platform()}",
        f"python: {platform.python_version()}",
        f"data_dir: {data_dir()}",
    ]
    try:
        import pynput  # noqa: F401

        ver = getattr(pynput, "__version__", None)
        if not ver:
            try:
                from importlib.metadata import version as _v

                ver = _v("pynput")
            except Exception:  # noqa: BLE001
                ver = "unknown"
        lines.append(f"pynput: {ver}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"pynput: IMPORT FAILED: {exc!r}")
    return lines


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version("bubble-buddy")
    except Exception:  # noqa: BLE001
        return "unknown"


def _config_summary() -> list[str]:
    try:
        from . import config as _config

        cfg = _config.load_config(reload=True)
    except Exception as exc:  # noqa: BLE001
        return [f"config: LOAD FAILED: {exc!r}"]
    keys = (
        "backend",
        "hotkey",
        "ui_language",
        "polish",
        "polish_engine",
        "launch_at_startup",
        "start_collapsed",
    )
    out = []
    for key in keys:
        if key in cfg:
            out.append(f"  {key}: {cfg.get(key)!r}")
    return ["config:"] + (out or ["  (no recognised keys)"])


def tail_log(max_lines: int = 200) -> str:
    """Return the last ``max_lines`` lines of the log file (best effort)."""
    path = log_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return f"(could not read log at {path}: {exc})"
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def snapshot(max_log_lines: int = 200) -> str:
    """Build a copyable diagnostics report: system info + config + recent log.

    This is what the "Copy diagnostics" affordance places on the clipboard so a
    user can paste a self-contained, debuggable report into the support skill.
    """
    parts: list[str] = []
    parts.append(f"# {APP_NAME} diagnostics")
    parts.append(f"generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    parts.append(f"log file: {log_path()}")
    parts.append("")
    parts.append("## system")
    parts.extend(_system_lines())
    parts.append("")
    parts.append("## " + "\n".join(_config_summary()))
    parts.append("")
    parts.append(f"## recent log (last {max_log_lines} lines)")
    parts.append(tail_log(max_log_lines))
    return "\n".join(parts)
