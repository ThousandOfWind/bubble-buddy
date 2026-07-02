"""Map the user's focused Copilot CLI terminal to its underlying session.

The Copilot CLI keeps a local store under ``~/.copilot``:

* ``session-store.db`` (SQLite) — a ``sessions`` table with
  ``id / cwd / repository / branch / summary / updated_at``.
* ``ide/*.lock`` (JSON) — one per connected IDE window, giving that window's
  open ``workspaceFolders`` (full paths).

When a Copilot CLI session runs inside a VS Code integrated terminal, the CLI
sets the *terminal tab title* to the session ``summary``. So we can bridge a
focused terminal to its exact session **deterministically**, with no VS Code
extension:

1. From the focused VS Code window *title* we recover the workspace folder.
2. Cross-check it against the open ``ide/*.lock`` workspaces to get the full
   ``cwd`` path.
3. Query ``session-store.db`` for sessions in that ``cwd``; if any candidate's
   ``summary`` appears in the focused UI text (window title / terminal tab
   accessible names), that is the exact session — otherwise fall back to the
   most-recently-updated session for that workspace.

Everything here is best-effort and read-only; any failure returns ``None``.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_APP_SUFFIXES = ("Visual Studio Code", "Code - OSS", "VSCodium", "Cursor")
# Trailing terminal/editor decorations we strip before folder matching.
_DIRTY_MARKERS = ("●", "•", "*")


@dataclass
class SessionMatch:
    """A resolved Copilot CLI session behind the focused terminal."""

    id: str = ""
    summary: str = ""
    repository: str = ""
    branch: str = ""
    cwd: str = ""
    exact: bool = False  # True when matched by summary (not just workspace)

    @property
    def is_empty(self) -> bool:
        return not self.id


def copilot_home() -> Path:
    """Location of the Copilot CLI state dir (override with COPILOT_HOME)."""
    env = os.environ.get("COPILOT_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".copilot"


def resolve_session(window_title: str, text_blob: str = "") -> SessionMatch | None:
    """Resolve the focused VS Code terminal to its Copilot CLI session.

    ``window_title`` is the foreground VS Code window title (used to recover the
    workspace folder). ``text_blob`` is any extra focused-UI text (terminal tab
    accessible names) that may contain the session summary for exact matching.
    Returns ``None`` when nothing can be resolved.
    """
    try:
        home = copilot_home()
        db = home / "session-store.db"
        if not db.exists():
            return None

        folder = _folder_from_title(window_title)
        cwd = _workspace_path(home, folder)
        candidates = _query_sessions(db, cwd=cwd, folder=folder)
        if not candidates:
            return None

        blob = f"{window_title}\n{text_blob}".strip()
        return _pick(candidates, blob)
    except BaseException:
        return None


# --------------------------------------------------------------------------- #
# Window-title → workspace folder
# --------------------------------------------------------------------------- #

def _folder_from_title(window_title: str) -> str:
    """Extract the workspace folder name from a VS Code window title.

    VS Code titles look like ``"<editor> - <folder> - Visual Studio Code"`` or
    ``"<folder> - Visual Studio Code"``. We drop the trailing app-name segment
    and take the last remaining segment as the workspace folder.
    """
    title = (window_title or "").strip()
    if not title:
        return ""
    parts = [p.strip() for p in title.split(" - ") if p.strip()]
    if not parts:
        return ""
    # Drop a trailing app-name segment (e.g. "Visual Studio Code").
    if len(parts) > 1 and any(parts[-1].endswith(s) for s in _APP_SUFFIXES):
        parts = parts[:-1]
    elif any(parts[-1].endswith(s) for s in _APP_SUFFIXES):
        return ""
    if not parts:
        return ""
    folder = parts[-1]
    for marker in _DIRTY_MARKERS:
        folder = folder.replace(marker, "")
    # Multi-root workspaces show "name (Workspace)"; keep the leading name.
    folder = folder.split(" (Workspace)")[0]
    return folder.strip()


def _workspace_path(home: Path, folder: str) -> str:
    """Cross-check ``folder`` against open ``ide/*.lock`` workspaces to recover the
    full workspace path (so we can match ``sessions.cwd`` exactly)."""
    if not folder:
        return ""
    try:
        import json

        ide_dir = home / "ide"
        if not ide_dir.is_dir():
            return ""
        want = folder.lower()
        for lock in ide_dir.glob("*.lock"):
            try:
                data = json.loads(lock.read_text(encoding="utf-8"))
            except BaseException:
                continue
            for ws in data.get("workspaceFolders", []) or []:
                if os.path.basename(str(ws).rstrip("/\\")).lower() == want:
                    return str(ws)
    except BaseException:
        return ""
    return ""


# --------------------------------------------------------------------------- #
# session-store.db queries
# --------------------------------------------------------------------------- #

def _connect_ro(db: Path) -> sqlite3.Connection:
    """Open the store read-only and lock-free (immutable) so a live CLI writing to
    it can't block or be disturbed by us."""
    uri = f"file:{db.as_posix()}?immutable=1"
    con = sqlite3.connect(uri, uri=True, timeout=1.0)
    return con


def _query_sessions(db: Path, cwd: str, folder: str) -> list[SessionMatch]:
    """Return workspace sessions (most-recent first) matching ``cwd`` when known,
    otherwise any session whose ``cwd`` basename equals ``folder``."""
    con = None
    try:
        con = _connect_ro(db)
        rows = con.execute(
            "SELECT id, cwd, repository, branch, summary FROM sessions "
            "WHERE cwd IS NOT NULL ORDER BY updated_at DESC"
        ).fetchall()
    except BaseException:
        return []
    finally:
        if con is not None:
            try:
                con.close()
            except BaseException:
                pass

    want_cwd = _norm_path(cwd)
    want_folder = (folder or "").lower()
    out: list[SessionMatch] = []
    for sid, scwd, repo, branch, summary in rows:
        if not sid or not scwd:
            continue
        if want_cwd:
            if _norm_path(scwd) != want_cwd:
                continue
        elif want_folder:
            if os.path.basename(str(scwd).rstrip("/\\")).lower() != want_folder:
                continue
        else:
            continue
        out.append(
            SessionMatch(
                id=str(sid),
                summary=str(summary or ""),
                repository=str(repo or ""),
                branch=str(branch or ""),
                cwd=str(scwd),
            )
        )
    return out


def _pick(candidates: list[SessionMatch], blob: str) -> SessionMatch | None:
    """Choose the exact session by summary match, else the most-recent workspace
    session (candidates are already ordered most-recent first).

    For exact matching we prefer the *longest* summary contained in the blob, so a
    specific title ("Fix parser bug") wins over a shorter one that is coincidentally
    a substring ("Fix"). Very short summaries are ignored to avoid matching generic
    words that happen to appear in the focused-UI text.
    """
    if not candidates:
        return None
    low = (blob or "").lower()
    if low:
        best: SessionMatch | None = None
        best_len = 0
        for match in candidates:
            summary = (match.summary or "").strip()
            if len(summary) < 4:
                continue
            if summary.lower() in low and len(summary) > best_len:
                best = match
                best_len = len(summary)
        if best is not None:
            best.exact = True
            return best
    return candidates[0]


def _norm_path(path: str) -> str:
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.normpath(str(path)))
    except BaseException:
        return str(path).lower()
