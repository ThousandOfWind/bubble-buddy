from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


COPILOT_HOME = Path.home() / ".copilot"
SESSION_STATE_DIR = COPILOT_HOME / "session-state"


def get_active_copilot_context(max_chars: int = 1400) -> str:
    session_id = find_active_copilot_session_id()
    if not session_id:
        return ""
    session_dir = SESSION_STATE_DIR / session_id
    if not session_dir.exists():
        return ""

    parts: list[str] = []
    workspace = read_workspace_summary(session_dir)
    if workspace:
        parts.append(workspace)

    plan = read_text(session_dir / "plan.md", max_chars=500)
    if plan:
        parts.append(f"当前计划：{compact(plan)}")

    recent = read_recent_messages(session_dir / "events.jsonl", max_chars=max_chars)
    if recent:
        parts.append(f"最近对话：{recent}")

    return compact(" | ".join(parts))[:max_chars]


def find_active_copilot_session_id() -> str:
    process_output = subprocess.run(
        ["ps", "-axo", "pid=,args="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    candidates: list[str] = []
    for line in process_output.splitlines():
        if "copilot" not in line:
            continue
        for pattern in (r"--resume[=\s]+([0-9a-fA-F-]{36})", r"--session-id[=\s]+([0-9a-fA-F-]{36})"):
            match = re.search(pattern, line)
            if match:
                candidates.append(match.group(1))

    existing = [sid for sid in candidates if (SESSION_STATE_DIR / sid / "events.jsonl").exists()]
    if not existing:
        return ""
    return max(existing, key=lambda sid: (SESSION_STATE_DIR / sid / "events.jsonl").stat().st_mtime)


def read_workspace_summary(session_dir: Path) -> str:
    path = session_dir / "workspace.yaml"
    if not path.exists():
        return ""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    fields = []
    for key in ("id", "cwd", "repository", "branch", "name"):
        if values.get(key):
            fields.append(f"{key}={values[key]}")
    return "；".join(fields)


def read_recent_messages(events_path: Path, max_chars: int) -> str:
    if not events_path.exists():
        return ""
    tail = read_tail(events_path, 512 * 1024)
    messages: list[str] = []
    for line in tail.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        data = event.get("data") or {}
        if event_type == "user.message":
            content = data.get("content") or data.get("transformedContent") or ""
            if content:
                messages.append(f"用户：{compact(str(content))[:240]}")
        elif event_type == "assistant.message":
            content = data.get("content") or ""
            if content:
                messages.append(f"助手：{compact(str(content))[:240]}")
    return " / ".join(messages[-8:])[-max_chars:]


def read_tail(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - max_bytes))
        data = handle.read()
    if size > max_bytes:
        data = data.split(b"\n", 1)[-1]
    return data.decode("utf-8", errors="replace")


def read_text(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:max_chars]


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
