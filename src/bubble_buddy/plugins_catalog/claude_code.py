"""Claude Code conversation transcript.

Detects Claude Code in a terminal, VS Code panel, or standalone Claude UI and
injects recent turns from its local ``~/.claude/projects`` transcript store.
Enabled by default and read-only.
"""

from __future__ import annotations

from pathlib import Path

from ..context_plugins import PluginInput, PluginResult

DEFAULT_ENABLED = True


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


class ClaudeCodePlugin:
    name = "claude_code"
    label = "Claude 会话记录"

    def __init__(
        self, max_turns: int = 6, per_message: int = 160, max_chars: int = 1100
    ) -> None:
        self.max_turns = max_turns
        self.per_message = per_message
        self.max_chars = max_chars

    def _resolve(self, ctx: PluginInput):
        from .. import claude_session, focus_context

        try:
            agent = focus_context.detect_coding_agent(
                ctx.title,
                list(ctx.ancestry or ()),
                ctx.exe_path or "",
                ctx.app_name,
            )
        except BaseException:
            agent = ""
        if agent != "claude":
            return None
        return claude_session.resolve_session(ctx.title, allow_latest=True)

    def matches(self, ctx: PluginInput) -> bool:
        return self._resolve(ctx) is not None

    def build_from_transcript(self, path: Path | str) -> PluginResult | None:
        from .. import claude_session

        turns = claude_session.recent_turns(path, limit=self.max_turns)
        blocks: list[str] = []
        total = 0
        for turn in reversed(turns):
            lines: list[str] = []
            user = _clip(turn.user_message, self.per_message)
            reply = _clip(turn.assistant_response, self.per_message)
            if user:
                lines.append(f"我：{user}")
            if reply:
                lines.append(f"Claude：{reply}")
            if not lines:
                continue
            block = "\n".join(lines)
            if blocks and total + len(block) > self.max_chars:
                break
            blocks.append(block)
            total += len(block)
        text = "\n".join(reversed(blocks)).strip()
        if not text:
            return None
        return PluginResult(
            name=self.name,
            label=self.label,
            text=text,
            resource=str(path),
        )

    def extract(self, ctx: PluginInput) -> PluginResult | None:
        match = self._resolve(ctx)
        return self.build_from_transcript(match.path) if match is not None else None


PLUGIN = ClaudeCodePlugin()
