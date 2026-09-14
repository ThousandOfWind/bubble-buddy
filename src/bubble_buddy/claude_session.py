"""Resolve and read local Claude Code conversation transcripts."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

_MAX_TRANSCRIPTS = 256
_CACHE_SECONDS = 3.0
_file_cache: tuple[Path | None, float, list[Path]] = (None, 0.0, [])
_turn_cache: dict[Path, tuple[int, int, list["Turn"]]] = {}


@dataclass
class SessionMatch:
    path: Path
    cwd: str = ""
    exact: bool = False


@dataclass
class Turn:
    user_message: str = ""
    assistant_response: str = ""


def claude_home() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def resolve_session(window_title: str, allow_latest: bool = False) -> SessionMatch | None:
    """Resolve the newest transcript matching the workspace in the window title."""
    try:
        from .copilot_session import _folder_from_title

        folder = _folder_from_title(window_title)
        candidates: list[SessionMatch] = []
        for path in _transcript_files(claude_home()):
            cwd = _read_cwd(path)
            match = SessionMatch(path=path, cwd=cwd)
            if folder and cwd and Path(cwd).name.casefold() == folder.casefold():
                match.exact = True
                return match
            candidates.append(match)
        return candidates[0] if allow_latest and candidates else None
    except BaseException:
        return None


def recent_turns(path: Path | str, limit: int = 6) -> list[Turn]:
    if not path or limit <= 0:
        return []
    try:
        transcript = Path(path)
        stat = transcript.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = _turn_cache.get(transcript)
        if cached and cached[:2] == signature:
            return cached[2][-limit:]

        turns: list[Turn] = []
        with transcript.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                role = str(item.get("type") or "")
                message = item.get("message")
                if not isinstance(message, dict):
                    continue
                text = _message_text(message.get("content"))
                if not text:
                    continue
                if role == "user":
                    turns.append(Turn(user_message=text))
                elif role == "assistant" and turns:
                    current = turns[-1].assistant_response
                    if text not in current:
                        turns[-1].assistant_response = f"{current}\n{text}".strip()

        _turn_cache[transcript] = (signature[0], signature[1], turns)
        return turns[-limit:]
    except BaseException:
        return []


def _transcript_files(home: Path) -> list[Path]:
    global _file_cache
    cached_home, cached_at, cached_files = _file_cache
    now = time.monotonic()
    if cached_home == home and now - cached_at < _CACHE_SECONDS:
        return list(cached_files)
    projects = home / "projects"
    if not projects.is_dir():
        return []
    files = sorted(
        projects.rglob("*.jsonl"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )[:_MAX_TRANSCRIPTS]
    _file_cache = (home, now, files)
    return list(files)


def _read_cwd(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(20):
                item = json.loads(handle.readline())
                cwd = item.get("cwd")
                if isinstance(cwd, str) and cwd:
                    return cwd
    except BaseException:
        pass
    return ""


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
