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
3. Query ``session-store.db`` for sessions in that ``cwd`` and pick the exact one:

   * **Preferred (deterministic):** each running CLI process writes a
     ``logs/process-<start>-<pid>.log`` whose header records both its working
     directory (``cwd=...``) and its session id (``Registering foreground
     session: <uuid>``). The most-recently-*modified* such log for the workspace
     identifies the session that is actually being used right now — this is far
     more reliable than the ``sessions.updated_at`` column, which the CLI does
     not bump on every write.
   * If a candidate's ``summary`` appears in the focused UI text (window title /
     terminal tab accessible names) we take that as an exact match too.
   * Otherwise fall back to the most-recently-updated session for the workspace.

Everything here is best-effort and read-only; any failure returns ``None``.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_APP_SUFFIXES = ("Visual Studio Code", "Code - OSS", "VSCodium", "Cursor")
# Trailing terminal/editor decorations we strip before folder matching.
_DIRTY_MARKERS = ("●", "•", "*")

# --- Active-session detection via CLI process logs -------------------------- #
# We only read the small header of each log, and only scan the most recent few.
_LOG_HEAD_BYTES = 16384
_MAX_LOGS_SCANNED = 24
_LOG_CWD_RE = re.compile(r"cwd=([^\r\n]+)")
_LOG_SESSION_RE = re.compile(
    r"Registering foreground session:\s*([0-9a-fA-F-]{36})"
)
_LOG_WORKSPACE_RE = re.compile(r"Workspace initialized:\s*([0-9a-fA-F-]{36})")


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


@dataclass
class Turn:
    """One conversation turn of a Copilot CLI session."""

    turn_index: int = 0
    user_message: str = ""
    assistant_response: str = ""


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
        blob = f"{window_title}\n{text_blob}".strip()
        active_id = _active_session_id_from_logs(home, cwd=cwd, folder=folder)

        candidates = _query_sessions(db, cwd=cwd, folder=folder)
        if candidates:
            return _pick(candidates, blob, active_id)

        # Fallback: the workspace folder could not be resolved from the window
        # title — e.g. a multi-root "Untitled (Workspace)" window, whose folder
        # segment ("Untitled") maps to no real path. The Copilot CLI terminal tab
        # title (== the session summary) is nonetheless present in the focused-UI
        # blob, so match any session whose summary appears there. We deliberately
        # match by summary ONLY (no recency fallback) to avoid attaching a random
        # session to a plain shell.
        return _match_by_summary(_all_sessions(db), blob)
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
# Active session via CLI process logs (deterministic)
# --------------------------------------------------------------------------- #

def _active_session_id_from_logs(home: Path, cwd: str, folder: str) -> str:
    """Return the session id of the most-recently-active CLI process for the
    focused workspace, read from ``~/.copilot/logs/process-*.log`` headers.

    Each CLI process logs its ``cwd`` and foreground session id at startup, and
    keeps writing to the same file, so the log's *mtime* is a live "last active"
    signal — much fresher than ``sessions.updated_at``. We scan only the most
    recent handful of logs and read only each file's small header. Returns ``""``
    when nothing matches (caller falls back to summary / recency)."""
    try:
        logs_dir = home / "logs"
        if not logs_dir.is_dir():
            return ""
        try:
            files = sorted(
                logs_dir.glob("process-*.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except BaseException:
            files = list(logs_dir.glob("process-*.log"))
        want_cwd = _norm_path(cwd)
        want_folder = (folder or "").lower()
        if not want_cwd and not want_folder:
            return ""
        for log in files[:_MAX_LOGS_SCANNED]:
            try:
                with open(log, "r", encoding="utf-8", errors="ignore") as fh:
                    head = fh.read(_LOG_HEAD_BYTES)
            except BaseException:
                continue
            m_cwd = _LOG_CWD_RE.search(head)
            if not m_cwd:
                continue
            log_cwd = m_cwd.group(1).strip()
            if want_cwd:
                if _norm_path(log_cwd) != want_cwd:
                    continue
            elif os.path.basename(log_cwd.rstrip("/\\")).lower() != want_folder:
                continue
            m_sess = _LOG_SESSION_RE.search(head) or _LOG_WORKSPACE_RE.search(head)
            if m_sess:
                return m_sess.group(1).lower()
    except BaseException:
        return ""
    return ""


# --------------------------------------------------------------------------- #
# session-store.db queries
# --------------------------------------------------------------------------- #

def _connect_ro(db: Path) -> sqlite3.Connection:
    """Open the store read-only in a WAL-aware way.

    The Copilot CLI runs the store in WAL mode: new turns are appended to the
    ``-wal`` sidecar and only folded into the main ``.db`` file at occasional
    checkpoints. ``?immutable=1`` is lock-free and fast but tells SQLite the file
    never changes, so it *ignores the WAL entirely* and returns a snapshot frozen
    at the last checkpoint — which made the live transcript stick at an old turn.

    ``mode=ro`` instead reads the WAL, so we see the turns the live CLI has just
    written. WAL readers use a shared read-mark and never block (nor are blocked
    by) the writer, so this stays non-disruptive. If the WAL-aware open fails
    (e.g. the ``-shm``/``-wal`` files are unreadable when no CLI is running), fall
    back to the immutable snapshot so reads still succeed with stale-but-present
    data."""
    posix = db.as_posix()
    try:
        con = sqlite3.connect(f"file:{posix}?mode=ro", uri=True, timeout=1.0)
        # Force a real read so a WAL/shm access failure surfaces here (and we can
        # fall back), not later mid-query.
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return con
    except BaseException:
        return sqlite3.connect(f"file:{posix}?immutable=1", uri=True, timeout=1.0)


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


def _all_sessions(db: Path) -> list[SessionMatch]:
    """Return every session (most-recent first), regardless of workspace. Used as
    a fallback when the focused window's workspace folder can't be resolved."""
    con = None
    try:
        con = _connect_ro(db)
        rows = con.execute(
            "SELECT id, cwd, repository, branch, summary FROM sessions "
            "ORDER BY updated_at DESC"
        ).fetchall()
    except BaseException:
        return []
    finally:
        if con is not None:
            try:
                con.close()
            except BaseException:
                pass
    out: list[SessionMatch] = []
    for sid, scwd, repo, branch, summary in rows:
        if not sid:
            continue
        out.append(
            SessionMatch(
                id=str(sid),
                summary=str(summary or ""),
                repository=str(repo or ""),
                branch=str(branch or ""),
                cwd=str(scwd or ""),
            )
        )
    return out


def _match_by_summary(
    candidates: list[SessionMatch], blob: str
) -> SessionMatch | None:
    """Return the candidate whose (>=4 char) ``summary`` is the *longest* one
    contained in ``blob``; ``None`` if none match. Marks the result ``exact``."""
    low = (blob or "").lower()
    if not low:
        return None
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


def _pick(
    candidates: list[SessionMatch], blob: str, active_id: str = ""
) -> SessionMatch | None:
    """Choose the exact session, preferring signals in order of reliability:

    1. The session summary appearing in the focused-UI ``blob`` (that text is the
       terminal tab title of the pane the user is actually looking at).
    2. The ``active_id`` derived from the freshest CLI process log for this
       workspace (deterministic "which session is running right now").
    3. Otherwise the most-recently-updated workspace session (candidates are
       already ordered most-recent first).

    For summary matching we prefer the *longest* summary contained in the blob, so
    a specific title ("Fix parser bug") wins over a shorter coincidental substring
    ("Fix"). Very short summaries are ignored to avoid matching generic words.
    """
    if not candidates:
        return None
    by_summary = _match_by_summary(candidates, blob)
    if by_summary is not None:
        return by_summary
    if active_id:
        for match in candidates:
            if match.id.lower() == active_id.lower():
                match.exact = True
                return match
    return candidates[0]


def _norm_path(path: str) -> str:
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.normpath(str(path)))
    except BaseException:
        return str(path).lower()


# --------------------------------------------------------------------------- #
# Conversation transcript (the `turns` table)
# --------------------------------------------------------------------------- #

def recent_turns(session_id: str, limit: int = 6) -> list[Turn]:
    """Return the last ``limit`` conversation turns of a Copilot CLI session,
    ordered oldest-first. Read-only and best-effort — returns ``[]`` on any
    failure (missing store, no session, locked db, ...)."""
    sid = (session_id or "").strip()
    if not sid:
        return []
    try:
        limit = max(1, int(limit))
    except BaseException:
        limit = 6
    con = None
    try:
        db = copilot_home() / "session-store.db"
        if not db.exists():
            return []
        con = _connect_ro(db)
        rows = con.execute(
            "SELECT turn_index, user_message, assistant_response FROM turns "
            "WHERE session_id = ? ORDER BY turn_index DESC LIMIT ?",
            (sid, limit),
        ).fetchall()
    except BaseException:
        return []
    finally:
        if con is not None:
            try:
                con.close()
            except BaseException:
                pass
    out = [
        Turn(
            turn_index=int(ti),
            user_message=str(um or ""),
            assistant_response=str(ar or ""),
        )
        for ti, um, ar in rows
    ]
    out.reverse()  # oldest-first so the transcript reads naturally
    return out
