"""Shared bubble contract for overlay frontends.

Both Qt and native AppKit render their own windows, but bubble semantics should
match: speech bubbles attach to the pet, context bubbles attach to the app badge,
and both carry the same stage/category accent and lifetime.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import frontend_style as style


class BubbleKind(str, Enum):
    SPEECH = "speech"
    CONTEXT = "context"
    GREETING = "greeting"


class BubbleAnchor(str, Enum):
    PET = "pet"
    APP_BADGE = "app_badge"


@dataclass(frozen=True)
class BubbleSpec:
    text: str
    kind: BubbleKind
    anchor: BubbleAnchor
    accent: str
    duration_ms: int
    max_width: int = 460
    min_width: int = 130


def make_bubble(
    text: str,
    *,
    kind: BubbleKind | str = BubbleKind.SPEECH,
    stage: str = "idle",
    accent: str = "",
    duration_ms: int | None = None,
) -> BubbleSpec:
    resolved_kind = kind if isinstance(kind, BubbleKind) else BubbleKind(str(kind))
    resolved_accent = accent or style.STAGE_COLORS.get(stage, style.STAGE_IDLE)
    anchor = BubbleAnchor.APP_BADGE if resolved_kind == BubbleKind.CONTEXT else BubbleAnchor.PET
    if duration_ms is None:
        duration_ms = 20_000 if resolved_kind == BubbleKind.SPEECH else 9_000
        if resolved_kind == BubbleKind.GREETING:
            duration_ms = 12_000
    return BubbleSpec(
        text=(text or "").strip(),
        kind=resolved_kind,
        anchor=anchor,
        accent=resolved_accent,
        duration_ms=duration_ms,
    )
