"""Resolve and read local pi / pi web conversation sessions."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

_MAX_SESSIONS = 256
_CACHE_SECONDS = 3.0
_session_cache: tuple[Path | None, float, list[Path]] = (None, 0.0, [])
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


def pi_agent_dir() -> Path:
    configured = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".pi" / "agent"


def pi_sessions_dir() -> Path:
    configured = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
    if configured:
        return Path(configured).expanduser()

    agent_dir = pi_agent_dir()
    settings_path = agent_dir / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except BaseException:
        data = {}
    session_dir = data.get("sessionDir")
    if isinstance(session_dir, str) and session_dir.strip():
        resolved = Path(session_dir).expanduser()
        return resolved if resolved.is_absolute() else (agent_dir / resolved)
    return agent_dir / "sessions"


def resolve_session(
    window_title: str,
    text_blob: str = "",
    allow_latest: bool = False,
    browser_url: str = "",
) -> SessionMatch | None:
    """Resolve the newest pi session matching the focused project/title.

    If the browser URL carries a pi session UUID, that wins as an exact match.
    """
    try:
        from .copilot_session import _folder_from_title

        explicit_id = _session_id_from_text(browser_url)
        folder = _folder_from_title(window_title)
        blob = f"{window_title}\n{text_blob}\n{browser_url}".lower()
        candidates = [
            match
            for path in _session_files(pi_sessions_dir())
            if (match := _read_session_meta(path)) is not None and match.path is not None
        ]

        # URL identity is authoritative. Search all files before considering cwd,
        # otherwise an unrelated newer session in the same project can win first.
        if explicit_id:
            for match in candidates:
                if match.id.casefold() == explicit_id.casefold():
                    match.exact = True
                    return match
            return None

        workspace_matches: list[SessionMatch] = []
        for match in candidates:
            cwd_name = Path(match.cwd).name.casefold() if match.cwd else ""
            if folder and cwd_name == folder.casefold():
                workspace_matches.append(match)
            elif cwd_name and len(cwd_name) >= 3 and cwd_name in blob:
                workspace_matches.append(match)

        # Avoid injecting a guessed transcript when several sessions share a cwd.
        # Wrong context is worse than no context; the browser URL should disambiguate.
        if len(workspace_matches) == 1:
            workspace_matches[0].exact = True
            return workspace_matches[0]
        if len(workspace_matches) > 1:
            return None
        return candidates[0] if allow_latest and candidates else None
    except BaseException:
        return None


def recent_turns(path: Path | str, limit: int = 6) -> list[Turn]:
    if not path or limit <= 0:
        return []
    try:
        session_path = Path(path)
        stat = session_path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = _turn_cache.get(session_path)
        if cached and cached[:2] == signature:
            return cached[2][-limit:]

        entries: list[dict] = []
        parents: dict[str, str | None] = {}
        current_id = ""
        with session_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                entry_type = str(item.get("type") or "")
                if entry_type == "session":
                    continue
                entry_id = str(item.get("id") or "")
                if not entry_id:
                    continue
                entries.append(item)
                parent_id = item.get("parentId")
                parents[entry_id] = str(parent_id) if parent_id is not None else None
                current_id = entry_id

        active_ids: set[str] = set()
        while current_id:
            active_ids.add(current_id)
            current_id = parents.get(current_id) or ""

        turns: list[Turn] = []
        for item in entries:
            entry_id = str(item.get("id") or "")
            if entry_id not in active_ids or item.get("type") != "message":
                continue
            message = item.get("message")
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            if role == "user":
                text = _message_text(message.get("content"))
                if text:
                    turns.append(Turn(user_message=text))
            elif role == "assistant":
                text = _message_text(message.get("content"))
                if not text or not turns:
                    continue
                current = turns[-1].assistant_response
                if text not in current:
                    turns[-1].assistant_response = f"{current}\n{text}".strip()

        _turn_cache[session_path] = (signature[0], signature[1], turns)
        return turns[-limit:]
    except BaseException:
        return []


def _session_files(directory: Path) -> list[Path]:
    global _session_cache
    cached_dir, cached_at, cached_files = _session_cache
    now = time.monotonic()
    if cached_dir == directory and now - cached_at < _CACHE_SECONDS:
        return list(cached_files)
    if not directory.is_dir():
        return []
    files = sorted(
        directory.rglob("*.jsonl"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )[:_MAX_SESSIONS]
    _session_cache = (directory, now, files)
    return list(files)


def _read_session_meta(path: Path) -> SessionMatch | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            item = json.loads(handle.readline())
        if item.get("type") != "session":
            return None
        session_id = str(item.get("id") or "")
        cwd = str(item.get("cwd") or "")
        if session_id and cwd:
            return SessionMatch(id=session_id, cwd=cwd, path=path)
    except BaseException:
        return None
    return None


def _session_id_from_text(text: str) -> str:
    import re

    match = re.search(r"([0-9a-fA-F-]{36})", text or "")
    return match.group(1) if match else ""


def _message_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)
