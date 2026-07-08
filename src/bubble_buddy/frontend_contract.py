"""Shared frontend contract for all overlay window engines.

The project has multiple window engines because macOS fullscreen Spaces require
native AppKit behavior while Windows can use Qt. This module defines the stable
state and feature vocabulary both frontends should speak so product behavior can
stay unified even when rendering differs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class Stage(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    LOADING_MODEL = "loading_model"
    STREAMING = "streaming"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    DONE = "done"
    ERROR = "error"


class FrontendFeature(str, Enum):
    FULLSCREEN_OVERLAY = "fullscreen_overlay"
    COLLAPSE_EXPAND = "collapse_expand"
    HOTKEY_WATCHDOG = "hotkey_watchdog"
    RECORD_BUTTONS = "record_buttons"
    RAW_POLISHED_TEXT = "raw_polished_text"
    COPY_RAW_POLISHED = "copy_raw_polished"
    HISTORY = "history"
    SETTINGS = "settings"
    POLISH_CATEGORY_EDITOR = "polish_category_editor"
    ACTIVE_CONTEXT_PANEL = "active_context_panel"
    LIVE_CONTEXT_ENRICHMENT = "live_context_enrichment"
    APP_BADGE = "app_badge"
    SPEECH_BUBBLE = "speech_bubble"
    AZURE_SIGN_IN = "azure_sign_in"
    REALTIME_AZURE = "realtime_azure"
    RELAUNCH = "relaunch"
    FIRST_LAUNCH_GREETING = "first_launch_greeting"


@dataclass(frozen=True)
class FrontendCapabilities:
    engine: str
    features: frozenset[FrontendFeature]

    def supports(self, feature: FrontendFeature) -> bool:
        return feature in self.features


@dataclass
class FrontendState:
    stage: Stage = Stage.IDLE
    hotkey: str = ""
    raw_text: str = ""
    polished_text: str = ""
    audio_path: str = ""
    error: str = ""
    copied: bool = False
    pasted: bool = False
    submitted: bool = False
    target_app: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def apply(self, patch: Mapping[str, object]) -> None:
        if "stage" in patch:
            try:
                self.stage = Stage(str(patch["stage"]))
            except ValueError:
                self.stage = Stage.ERROR
                self.error = f"Unknown frontend stage: {patch['stage']}"
        if "hotkey" in patch:
            self.hotkey = str(patch["hotkey"])
        if "raw_text" in patch:
            self.raw_text = str(patch["raw_text"] or "")
        if "plain_text" in patch and "raw_text" not in patch:
            self.raw_text = str(patch["plain_text"] or "")
        if "rephrased_text" in patch:
            self.polished_text = str(patch["rephrased_text"] or "")
        if "polished_text" in patch:
            self.polished_text = str(patch["polished_text"] or "")
        if "audio_path" in patch:
            self.audio_path = str(patch["audio_path"] or "")
        if "error" in patch:
            self.error = str(patch["error"] or "")
        for key in ("copied", "pasted", "submitted"):
            if key in patch:
                setattr(self, key, bool(patch[key]))
        if "target_app" in patch:
            self.target_app = str(patch["target_app"] or "")
        for key, value in patch.items():
            if key not in {
                "stage",
                "hotkey",
                "raw_text",
                "plain_text",
                "rephrased_text",
                "polished_text",
                "audio_path",
                "error",
                "copied",
                "pasted",
                "submitted",
                "target_app",
            }:
                self.extras[key] = value

    def snapshot(self) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "hotkey": self.hotkey,
            "raw_text": self.raw_text,
            "plain_text": self.raw_text,
            "rephrased_text": self.polished_text,
            "audio_path": self.audio_path,
            "error": self.error,
            "copied": self.copied,
            "pasted": self.pasted,
            "submitted": self.submitted,
            "target_app": self.target_app,
            **self.extras,
        }


QT_CAPABILITIES = FrontendCapabilities(
    engine="qt",
    features=frozenset({
        FrontendFeature.COLLAPSE_EXPAND,
        FrontendFeature.HOTKEY_WATCHDOG,
        FrontendFeature.RECORD_BUTTONS,
        FrontendFeature.RAW_POLISHED_TEXT,
        FrontendFeature.COPY_RAW_POLISHED,
        FrontendFeature.HISTORY,
        FrontendFeature.SETTINGS,
        FrontendFeature.POLISH_CATEGORY_EDITOR,
        FrontendFeature.ACTIVE_CONTEXT_PANEL,
        FrontendFeature.LIVE_CONTEXT_ENRICHMENT,
        FrontendFeature.APP_BADGE,
        FrontendFeature.SPEECH_BUBBLE,
        FrontendFeature.AZURE_SIGN_IN,
        FrontendFeature.REALTIME_AZURE,
        FrontendFeature.RELAUNCH,
        FrontendFeature.FIRST_LAUNCH_GREETING,
    }),
)


MAC_NATIVE_CAPABILITIES = FrontendCapabilities(
    engine="mac_native",
    features=frozenset({
        FrontendFeature.FULLSCREEN_OVERLAY,
        FrontendFeature.COLLAPSE_EXPAND,
        FrontendFeature.RECORD_BUTTONS,
        FrontendFeature.RAW_POLISHED_TEXT,
        FrontendFeature.COPY_RAW_POLISHED,
        FrontendFeature.HISTORY,
        FrontendFeature.SETTINGS,
        FrontendFeature.ACTIVE_CONTEXT_PANEL,
        FrontendFeature.AZURE_SIGN_IN,
        FrontendFeature.REALTIME_AZURE,
        FrontendFeature.RELAUNCH,
        FrontendFeature.FIRST_LAUNCH_GREETING,
    }),
)
