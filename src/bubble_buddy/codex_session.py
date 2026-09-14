"""Resolve and read local OpenAI Codex CLI conversation rollouts."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

_MAX_ROLLOUTS = 256
_ROLLOUT_CACHE_SECONDS = 3.0
_rollout_cache: tuple[Path | None, float, list[Path]] = (None, 0.0, [])
_turn_cache: dict[Path, tuple[int, int, list["Turn"]]] = {}


@dataclass
class SessionMatch:
    id: str = ""
    cwd: str = ""
    path: Path | None = None
    exact: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.id or self.path is None


@dataclass
class Turn:
    user_message: str = ""
    assistant_response: str = ""


def codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".codex"


def resolve_session(window_title: str, allow_latest: bool = False) -> SessionMatch | None:
    """Return the most recently active rollout for the focused workspace."""
    try:
        from .copilot_session import _folder_from_title

        folder = _folder_from_title(window_title)
        candidates: list[SessionMatch] = []
        for path in _rollout_files(codex_home()):
            match = _read_session_meta(path)
            if match is None:
                continue
            if folder and Path(match.cwd).name.casefold() == folder.casefold():
                match.exact = True
                return match
            candidates.append(match)
        if allow_latest and candidates:
            return candidates[0]
    except BaseException:
        return None
    return None


def recent_turns(path: Path | str, limit: int = 6) -> list[Turn]:
    """Read recent user/final-agent turns from a Codex rollout JSONL file."""
    if not path or limit <= 0:
        return []
    try:
        rollout = Path(path)
        stat = rollout.stat()
        cached = _turn_cache.get(rollout)
        signature = (stat.st_mtime_ns, stat.st_size)
        if cached and cached[:2] == signature:
            return cached[2][-limit:]

        turns: list[Turn] = []
        with rollout.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "event_msg" not in line:
                    continue
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if item.get("type") != "event_msg":
                    continue
                payload = item.get("payload")
                if not isinstance(payload, dict):
                    continue
                event_type = payload.get("type")
                message = payload.get("message")
                if not isinstance(message, str) or not message.strip():
                    continue
                if event_type == "user_message":
                    turns.append(Turn(user_message=message.strip()))
                elif event_type == "agent_message" and turns:
                    if str(payload.get("phase") or "").casefold() == "commentary":
                        continue
                    reply = message.strip()
                    current = turns[-1].assistant_response
                    if not current:
                        turns[-1].assistant_response = reply
                    elif reply not in current:
                        turns[-1].assistant_response = f"{current}\n{reply}"

        _turn_cache[rollout] = (signature[0], signature[1], turns)
        return turns[-limit:]
    except BaseException:
        return []


def _rollout_files(home: Path) -> list[Path]:
    global _rollout_cache
    cached_home, cached_at, cached_files = _rollout_cache
    now = time.monotonic()
    if cached_home == home and now - cached_at < _ROLLOUT_CACHE_SECONDS:
        return list(cached_files)

    sessions = home / "sessions"
    if not sessions.is_dir():
        return []
    files = sorted(
        sessions.rglob("rollout-*.jsonl"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )[:_MAX_ROLLOUTS]
    _rollout_cache = (home, now, files)
    return list(files)


def _read_session_meta(path: Path) -> SessionMatch | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(8):
                line = handle.readline()
                if not line:
                    break
                item = json.loads(line)
                if item.get("type") != "session_meta":
                    continue
                payload = item.get("payload")
                if not isinstance(payload, dict):
                    return None
                meta = payload.get("meta")
                if isinstance(meta, dict):
                    payload = meta
                session_id = str(payload.get("id") or payload.get("session_id") or "")
                cwd = str(payload.get("cwd") or "")
                if session_id and cwd:
                    return SessionMatch(id=session_id, cwd=cwd, path=path)
                return None
    except BaseException:
        return None
    return None
