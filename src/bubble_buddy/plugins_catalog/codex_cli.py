"""Codex coding-agent conversation transcript.

Detects OpenAI Codex in a terminal, VS Code panel, or its standalone app and
reads recent user and final-agent messages from its local rollout store.

Enabled by default. Reads ``$CODEX_HOME`` (or ``~/.codex``) read-only.
"""

from __future__ import annotations

from pathlib import Path

from ..context_plugins import PluginInput, PluginResult

DEFAULT_ENABLED = True


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


class CodexCliPlugin:
    name = "codex_cli"
    label = "Codex 会话记录"

    def __init__(
        self, max_turns: int = 6, per_message: int = 160, max_chars: int = 1100
    ) -> None:
        self.max_turns = max_turns
        self.per_message = per_message
        self.max_chars = max_chars

    def _resolve(self, ctx: PluginInput):
        from .. import codex_session, focus_context

        chain = list(ctx.ancestry or ())
        try:
            agent = focus_context.detect_coding_agent(
                ctx.title, chain, ctx.exe_path or "", ctx.app_name
            )
        except BaseException:
            agent = ""
        if agent != "codex":
            return None
        return codex_session.resolve_session(ctx.title, allow_latest=True)

    def matches(self, ctx: PluginInput) -> bool:
        match = self._resolve(ctx)
        return bool(match and not match.is_empty)

    def build_from_rollout(self, path: Path | str) -> "PluginResult | None":
        from .. import codex_session

        turns = codex_session.recent_turns(path, limit=self.max_turns)
        if not turns:
            return None

        blocks: list[str] = []
        total = 0
        for turn in reversed(turns):
            lines: list[str] = []
            user = _clip(turn.user_message, self.per_message)
            reply = _clip(turn.assistant_response, self.per_message)
            if user:
                lines.append(f"我：{user}")
            if reply:
                lines.append(f"Codex：{reply}")
            if not lines:
                continue
            block = "\n".join(lines)
            if blocks and total + len(block) > self.max_chars:
                break
            blocks.append(block)
            total += len(block)

        blocks.reverse()
        text = "\n".join(blocks).strip()
        if not text:
            return None
        return PluginResult(
            name=self.name,
            label=self.label,
            text=text,
            resource=str(path),
        )

    def extract(self, ctx: PluginInput) -> "PluginResult | None":
        match = self._resolve(ctx)
        if match is None or match.path is None:
            return None
        return self.build_from_rollout(match.path)


PLUGIN = CodexCliPlugin()
