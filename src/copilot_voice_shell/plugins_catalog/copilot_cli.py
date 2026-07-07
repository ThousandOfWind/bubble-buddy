"""Copilot CLI conversation transcript.

Detects a Copilot CLI session running inside a VS Code integrated terminal (or a
dedicated terminal) and loads the recent conversation transcript into the active
context. This lets the polisher translate and clean up your dictation
consistently with the terms and topic already used in that session, instead of
guessing each utterance blind.

It works only from the *native* focus input (window title + raw control
ancestry): the plugin resolves the CLI session and confirms the focused pane by
itself, so nothing Copilot-specific has to be pre-computed for it.

The transcript is a sliding window of the most recent turns: whole oldest turns
are dropped to fit the budget (never a mid-sentence cut), and each message is
clipped so one long reply can't crowd out the rest.

Enabled by default. Reads the local Copilot CLI store (``~/.copilot``)
read-only.
"""

from __future__ import annotations

from ..context_plugins import PluginInput, PluginResult

DEFAULT_ENABLED = True


def _clip(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


class CopilotCliPlugin:
    name = "copilot_cli"
    label = "Copilot 会话记录"

    def __init__(
        self, max_turns: int = 6, per_message: int = 160, max_chars: int = 1100
    ) -> None:
        self.max_turns = max_turns  # how many recent turns to consider
        self.per_message = per_message  # per-message clip so long replies don't dominate
        self.max_chars = max_chars  # total budget; whole oldest turns drop to fit

    def _resolve(self, ctx: PluginInput) -> tuple[bool, str]:
        """Interpret the raw focus context ourselves: find the Copilot CLI session
        behind the focused terminal and confirm the *focused pane* really is it.
        Returns ``(is_copilot_pane, session_id)``. Fully guarded."""
        from .. import copilot_session, focus_context

        chain = list(ctx.ancestry or ())
        exe = (ctx.exe_path or "").lower()
        blob = "\n".join(name for _t, name, _c in chain if name)
        try:
            match = copilot_session.resolve_session(ctx.title, f"{ctx.title}\n{blob}")
        except BaseException:
            match = None
        summary = getattr(match, "summary", "") if match else ""
        session_id = getattr(match, "id", "") if match else ""
        try:
            is_cli = bool(focus_context.detect_copilot_cli(ctx.title, chain, summary, exe))
        except BaseException:
            is_cli = False
        return is_cli, session_id

    def matches(self, ctx: PluginInput) -> bool:
        is_cli, session_id = self._resolve(ctx)
        return bool(is_cli and session_id)

    def build_from_session(self, session_id: str) -> "PluginResult | None":
        """Build the sliding-window transcript result for an already-resolved
        session id. Split out from :meth:`extract` so the live overlay can cheaply
        refresh the transcript (a plain DB read) as the conversation advances,
        without re-running the expensive UIA focus walk that :meth:`extract` needs.
        """
        from .. import copilot_session

        if not session_id:
            return None
        turns = copilot_session.recent_turns(session_id, limit=self.max_turns)
        if not turns:
            return None

        # Walk newest-first, keeping whole turns until we hit the budget, then flip
        # back to oldest-first so the window reads naturally. This is a true sliding
        # window: as the conversation grows, the oldest kept turn falls off cleanly.
        blocks: list[str] = []
        total = 0
        for turn in reversed(turns):
            lines: list[str] = []
            user = _clip(turn.user_message, self.per_message)
            reply = _clip(turn.assistant_response, self.per_message)
            if user:
                lines.append(f"我：{user}")
            if reply:
                lines.append(f"Copilot：{reply}")
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
        return PluginResult(name=self.name, label=self.label, text=text)

    def extract(self, ctx: PluginInput) -> "PluginResult | None":
        _is_cli, session_id = self._resolve(ctx)
        return self.build_from_session(session_id)


PLUGIN = CopilotCliPlugin()
