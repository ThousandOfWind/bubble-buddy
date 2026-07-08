"""Shared visual tokens for Qt and native overlay engines."""
from __future__ import annotations

CARD_BG = "#0A1221"
PANEL_BG = "#080D1C"
FIELD_BG = "#0B1428"
BORDER = "#22314F"
FIELD_BORDER = "#1B2740"
BUTTON_BG = "#16223A"
BUTTON_BORDER = "#2A3A5C"
BUTTON_HOVER = "#22335A"
BUTTON_PRESSED = "#2C3F6B"
TEXT = "#E8ECF6"
TEXT_MUTED = "#9EB0E0"
ERROR_TEXT = "#FFBDC4"

ORB_BODY = "#6E9BFF"
ORB_INK = "#20304F"

STAGE_IDLE = "#6E9BFF"
STAGE_RECORDING = "#FF4D67"
STAGE_WORKING = "#B57CFF"
STAGE_DONE = "#39D98A"
STAGE_ERROR = "#FFA426"

STAGE_COLORS = {
    "idle": STAGE_IDLE,
    "recording": STAGE_RECORDING,
    "loading_model": STAGE_WORKING,
    "streaming": STAGE_WORKING,
    "transcribing": STAGE_WORKING,
    "transcribed": STAGE_WORKING,
    "done": STAGE_DONE,
    "error": STAGE_ERROR,
}

STAGE_VISUAL = {
    "idle": "idle",
    "recording": "recording",
    "loading_model": "thinking",
    "streaming": "recording",
    "transcribing": "thinking",
    "transcribed": "thinking",
    "done": "done",
    "error": "error",
}

VISUAL_GLOW = {
    "idle": STAGE_IDLE,
    "recording": STAGE_RECORDING,
    "thinking": STAGE_WORKING,
    "done": STAGE_DONE,
    "error": STAGE_ERROR,
}

# Semantic glow intensity. Idle should be a very subtle presence indicator; the
# visible/pulsing aura is reserved for active states, especially recording.
GLOW_ALPHA_IDLE = 0.15
GLOW_ALPHA_RECORDING = 0.50
GLOW_ALPHA_WORKING_MIN = 0.30
GLOW_ALPHA_WORKING_MAX = 0.46
GLOW_ALPHA_DONE = 0.55
GLOW_ALPHA_ERROR = 0.50

ICON_BUTTON_SIZE = (44, 40)
ICON_BUTTON_RADIUS = 12
