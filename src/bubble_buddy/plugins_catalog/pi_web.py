"""pi / pi web coding-agent conversation transcript.

Detects pi in a browser tab or other supported surface and injects recent turns
from its local session store. Enabled by default and read-only. Reads
``PI_CODING_AGENT_SESSION_DIR`` when set, otherwise the configured
``sessionDir`` from ``~/.pi/agent/settings.json`` (or the default
``~/.pi/agent/sessions``).
"""

from __future__ import annotations

from pathlib import Path

from ..context_plugins import PluginInput, PluginResult

DEFAULT_ENABLED = True


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


class PiWebPlugin:
    name = "pi_web"
    label = "pi 会话记录"

    def __init__(
        self, max_turns: int = 6, per_message: int = 160, max_chars: int = 1100
    ) -> None:
        self.max_turns = max_turns
        self.per_message = per_message
        self.max_chars = max_chars

    def _resolve(self, ctx: PluginInput):
        from .. import focus_context, pi_session

        chain = list(ctx.ancestry or ())
        blob = "\n".join(name for _t, name, _c in chain if name)
        browser_url = (getattr(ctx, "browser_url", "") or "").strip()
        try:
            agent = focus_context.detect_coding_agent(
                ctx.title,
                chain,
                ctx.exe_path or "",
                ctx.app_name,
            )
        except BaseException:
            agent = ""
        if agent != "pi":
            return None
        return pi_session.resolve_session(
            ctx.title,
            blob,
            allow_latest=True,
            browser_url=browser_url,
        )

    def matches(self, ctx: PluginInput) -> bool:
        match = self._resolve(ctx)
        return bool(match and not match.is_empty)

    def build_from_session(self, path: Path | str) -> PluginResult | None:
        from .. import pi_session

        turns = pi_session.recent_turns(path, limit=self.max_turns)
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
                lines.append(f"Pi：{reply}")
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

    def extract(self, ctx: PluginInput) -> PluginResult | None:
        match = self._resolve(ctx)
        if match is None or match.path is None:
            return None
        return self.build_from_session(match.path)


PLUGIN = PiWebPlugin()
