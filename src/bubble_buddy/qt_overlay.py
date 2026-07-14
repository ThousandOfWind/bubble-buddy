from __future__ import annotations

import os
import math
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pyperclip
import sounddevice as sd
import soundfile as sf
from pynput import keyboard
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtCore import QTimer, QSize, QPoint, QPointF, QRectF, QFileInfo
from PySide6.QtCore import (
    QPropertyAnimation,
    QEasingCurve,
    QSequentialAnimationGroup,
    QParallelAnimationGroup,
    QAbstractAnimation,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QBrush, QPolygonF, QFontMetrics
from PySide6.QtGui import QPen, QPixmap, QIcon, QCursor, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCheckBox,
    QFileIconProvider,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QGraphicsDropShadowEffect,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import config as _config
from . import focus_context
from .i18n import t, set_language, current_language, resolve_language
from .cli import (
    DEFAULT_HF_ENDPOINT,
    apply_replacements,
    load_replacements,
    merge_segment_text,
    normalize_hotkey,
    transcribe_audio_mlx,
    polish_text,
)
from .platform_services import FocusInfo, get_platform_services
from . import frontend_style as _style
from .frontend_bubble import BubbleKind, BubbleSpec, make_bubble


SAMPLE_RATE = 16_000
SILENT_PEAK_THRESHOLD = 1e-6


@dataclass(frozen=True)
class FocusTarget:
    system: str
    bundle_id: str = ""
    name: str = ""
    pid: int = 0
    hwnd: int = 0
    exe_path: str = ""
    title: str = ""
    sub_kind: str = ""
    content: str = ""
    session: object = None  # focus_context.SessionInfo | None (resolved CLI session)
    copilot_cli: bool = False  # confident: the FOCUSED pane is a Copilot CLI terminal
    plugins: tuple = ()  # context_plugins.PluginResult tuple (per-app extra context)


def _session_line(session: object) -> str:
    """Format a resolved Copilot CLI session for the context panel/prompt."""
    if session is None:
        return ""
    summary = (getattr(session, "summary", "") or "").strip()
    repo = (getattr(session, "repository", "") or "").strip()
    branch = (getattr(session, "branch", "") or "").strip()
    if not (summary or repo):
        return ""
    label = summary or t("ctx.session_unnamed")
    meta = []
    if repo:
        meta.append(repo)
    if branch:
        meta.append(branch)
    tail = f"（{' · '.join(meta)}）" if meta else ""
    hint = "" if getattr(session, "exact", False) else "≈"
    return f"{t('ctx.session')}：{hint}{label}{tail}"


class AudioRecorder:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                raise RuntimeError("Recording is already in progress.")
            self._chunks = []
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                blocksize=SAMPLE_RATE // 10,
                latency="high",
                channels=1,
                dtype="float32",
                callback=self._on_audio,
            )
            self._stream.start()

    def stop(self) -> Path:
        with self._lock:
            if self._stream is None:
                raise RuntimeError("Recording is not in progress.")
            self._stream.stop()
            self._stream.close()
            self._stream = None

            if not self._chunks:
                raise RuntimeError("No audio captured from microphone.")

            audio = np.concatenate(self._chunks, axis=0)
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak <= SILENT_PEAK_THRESHOLD:
                raise RuntimeError(
                    "Recording captured only silence. Check your system's "
                    "microphone permission and the selected input device."
                )
            audio_path = Path(tempfile.gettempdir()) / "bubble-buddy" / f"qt-recording-{int(time.time())}.wav"
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(audio_path, audio, SAMPLE_RATE)
            return audio_path

    def is_recording(self) -> bool:
        return self._stream is not None

    def _on_audio(self, indata: np.ndarray, _frames: int, _time_info, status) -> None:
        if status:
            # Keep recording; surface errors on stop if no audio was captured.
            pass
        self._chunks.append(indata.copy())


class TranscribeWorker(QThread):
    finished_text = Signal(str, str)
    failed = Signal(str)

    def __init__(
        self,
        audio_path: Path,
        model_name: str,
        backend: str,
        mlx_model: str,
        language: str,
        hf_endpoint: str,
        replacement_pairs: list[str],
        replacements_file: Path | None,
        polish: str,
        context_file: Path | None,
        session_context: bool,
        language_preference: str,
        polish_engine: str,
        ollama_model: str,
        target_app_name: str | None = None,
        target_app_bundle_id: str | None = None,
        live_context: str = "",
        focus_sub_kind: str = "",
        copilot_session: bool = False,
    ) -> None:
        super().__init__()
        self.audio_path = audio_path
        self.model_name = model_name
        self.backend = backend
        self.mlx_model = mlx_model
        self.language = language
        self.hf_endpoint = hf_endpoint
        self.replacement_pairs = replacement_pairs
        self.replacements_file = replacements_file
        self.polish = polish
        self.context_file = context_file
        self.session_context = session_context
        self.language_preference = language_preference
        self.polish_engine = polish_engine
        self.ollama_model = ollama_model
        self.target_app_name = target_app_name
        self.target_app_bundle_id = target_app_bundle_id
        self.live_context = live_context
        self.focus_sub_kind = focus_sub_kind
        self.copilot_session = copilot_session

    def run(self) -> None:
        try:
            # The dictated speech language is derived from the single "Speech
            # language" preference (zh-en -> auto-detect), so local Whisper and
            # Azure stay in sync and there is no separate "language hint" setting.
            from . import azure_client

            local_lang = azure_client.transcribe_language_hint(self.language_preference) or None
            if self.backend == "mlx":
                result = transcribe_audio_mlx(
                    self.audio_path,
                    local_lang,
                    self.mlx_model,
                    replacement_pairs=self.replacement_pairs,
                    replacements_file=self.replacements_file,
                )
                raw_text = str(result["plain_text"])
            elif self.backend == "azure":
                from .cli import transcribe_audio_azure

                result = transcribe_audio_azure(
                    self.audio_path,
                    self.language,
                    replacement_pairs=self.replacement_pairs,
                    replacements_file=self.replacements_file,
                    language_preference=self.language_preference,
                )
                raw_text = str(result["plain_text"])
            else:
                # Imported lazily so lean (Azure-only) builds that exclude the
                # local Whisper stack still start; only reached for local backend.
                try:
                    from faster_whisper import WhisperModel
                except ImportError:
                    from .i18n import t as _t

                    raise RuntimeError(_t("msg.local_engine_missing"))

                model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                segments, _info = model.transcribe(str(self.audio_path), language=local_lang)
                replacements = load_replacements(self.replacements_file, self.replacement_pairs)
                texts = [apply_replacements(segment.text.strip(), replacements) for segment in segments if segment.text.strip()]
                raw_text = merge_segment_text(texts)
            polished = polish_text(
                raw_text,
                self.polish,
                self.context_file,
                session_context=self.session_context,
                language_preference=self.language_preference,
                engine=self.polish_engine,
                ollama_model=self.ollama_model,
                target_app_name=self.target_app_name,
                target_app_bundle_id=self.target_app_bundle_id,
                live_context=self.live_context,
                focus_sub_kind=self.focus_sub_kind,
                copilot_session=self.copilot_session,
            )
            self.finished_text.emit(raw_text, polished)
        except BaseException as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


REALTIME_SAMPLE_RATE = 24_000

ICON_COLOR = "#DCE6FF"


def _rt_log(msg: str) -> None:
    """Append a realtime-streaming diagnostic line to a log file when the
    COPILOT_RT_DEBUG environment variable is set. No-op otherwise."""
    path = os.environ.get("COPILOT_RT_DEBUG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} +{time.perf_counter():.3f} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


class SpeechBubble(QWidget):
    """A frameless, translucent speech bubble with a tail pointing at the orb.
    Sizes itself to the text via font metrics so it renders correctly on the very
    first show (no small-then-resize flash) and grows as more words arrive. The
    tail can point down/up/left/right; for a bubble to the right of the orb the
    tail points left. A soft drop shadow gives a flat-but-lifted look (no gradient,
    no visible border seam)."""

    PAD_X = 16
    PAD_Y = 12
    TAIL_W = 18
    TAIL_H = 12
    RADIUS = 15
    MIN_TEXT_W = 130  # keep short context readable rather than a tiny pill
    MAX_TEXT_W = 460
    ACCENT_W = 5  # horizontal room reserved for the accent bar (bar + gap)
    SHADOW = 18  # transparent margin reserved around the shape for the drop shadow

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._text = ""
        self._tail_side = "left"  # bottom | top | left | right
        self._font = QFont("Segoe UI", 12)
        self._body_w = 0
        self._body_h = 0
        self._accent: QColor | None = None

        # Elastic reveal: the bubble springs out of its tail tip (the point that
        # touches the orb/badge) with a little overshoot, and retracts back into it
        # on hide — so it feels like it pops out of the pet rather than fading in.
        self._grow = _Spring(1.0, 15.0, 0.40)
        self._opacity = 1.0
        self._hiding = False
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._tick)
        self._last = 0.0

        # NOTE: intentionally NO QGraphicsDropShadowEffect. On a translucent frameless
        # top-level window that effect fails to repaint newly-exposed area when the
        # widget grows live (so a streaming bubble appears "stuck" at its old size
        # while text overflows invisibly). We paint a soft shadow manually instead.

    def set_text(self, text: str) -> None:
        """Update the bubble text and recompute its size from font metrics."""
        self._text = text or ""
        fm = QFontMetrics(self._font)
        flags = int(Qt.TextFlag.TextWordWrap)
        # The accent bar steals ACCENT_W px of horizontal room from the text region in
        # paintEvent. Reserve the identical amount here so the width we MEASURE the
        # wrapped text at matches the width we DRAW it at; otherwise, right at a wrap
        # boundary, drawing wraps one line more than measured and the last line gets
        # clipped until the next update ("box doesn't grow in time when it wraps").
        accent_extra = self.ACCENT_W if self._accent else 0
        rect = fm.boundingRect(0, 0, self.MAX_TEXT_W, 10_000, flags, self._text)
        text_w = min(max(rect.width(), self.MIN_TEXT_W), self.MAX_TEXT_W)
        text_h = max(rect.height(), fm.height())
        self._body_w = text_w + 2 * self.PAD_X + accent_extra
        self._body_h = text_h + 2 * self.PAD_Y
        if self._tail_side in ("left", "right"):
            total_w = self._body_w + self.TAIL_H
            total_h = self._body_h
        else:
            total_w = self._body_w
            total_h = self._body_h + self.TAIL_H
        m = self.SHADOW
        self.resize(total_w + 2 * m, total_h + 2 * m)
        self.update()

    def _tail_tip_local(self) -> QPointF:
        """Local position of the tail tip (the point that touches the orb/badge)."""
        o = self._body_origin()
        cx = o.x() + self._body_w / 2
        cy = o.y() + self._body_h / 2
        if self._tail_side == "bottom":
            return QPointF(cx, o.y() + self._body_h + self.TAIL_H)
        elif self._tail_side == "top":
            return QPointF(cx, o.y() - self.TAIL_H)
        elif self._tail_side == "left":
            return QPointF(o.x() - self.TAIL_H, cy)
        else:  # right
            return QPointF(o.x() + self._body_w + self.TAIL_H, cy)

    def tail_tip_global(self) -> QPointF:
        """Global position of the tail tip (the point that touches the orb)."""
        return self.mapToGlobal(self._tail_tip_local().toPoint())

    def pop_in(self) -> None:
        """Spring the bubble out of its tail tip with an elastic overshoot."""
        self._hiding = False
        self._opacity = 0.0
        self.setWindowOpacity(0.0)
        self._grow.x = 0.3
        self._grow.v = 0.0
        self._grow.set(1.0)
        self.show()
        self.raise_()
        self._last = time.perf_counter()
        if not self._anim.isActive():
            self._anim.start()

    def pop_out(self) -> None:
        """Retract the bubble back into its tail tip, then hide it."""
        if not self.isVisible():
            return
        self._hiding = True
        self._grow.set(0.2)
        self._last = time.perf_counter()
        if not self._anim.isActive():
            self._anim.start()

    def ensure_shown(self) -> None:
        """Make sure the bubble is fully visible (cancel any in-flight retract)."""
        self._hiding = False
        self._opacity = 1.0
        self.setWindowOpacity(1.0)
        self._grow.set(1.0)
        self.show()
        self.raise_()

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        self._grow.step(dt)
        target = 0.0 if self._hiding else 1.0
        self._opacity += (target - self._opacity) * min(1.0, dt * 16.0)
        self.setWindowOpacity(max(0.0, min(1.0, self._opacity)))
        self.update()
        if self._hiding:
            if self._opacity < 0.05:
                self._anim.stop()
                self.hide()
                self._hiding = False
                self._opacity = 1.0
                self.setWindowOpacity(1.0)
                self._grow.x = 1.0
                self._grow.v = 0.0
        elif abs(self._grow.x - 1.0) < 0.006 and abs(self._grow.v) < 0.02 and self._opacity > 0.98:
            self._grow.x = 1.0
            self._grow.v = 0.0
            self._anim.stop()

    def _body_origin(self) -> QPointF:
        """Top-left of the body rect within the widget (accounting for tail + shadow margin)."""
        m = self.SHADOW
        left = m + (self.TAIL_H if self._tail_side == "left" else 0)
        top = m + (self.TAIL_H if self._tail_side == "top" else 0)
        return QPointF(left, top)

    def set_tail_side(self, side: str) -> None:
        if side != self._tail_side:
            self._tail_side = side
            if self._text:
                self.set_text(self._text)
            else:
                self.update()

    def set_accent(self, color) -> None:
        """A category/stage color shown as a slim bar on the tail side of the bubble
        so it visually ties to the pet/app it belongs to."""
        had = self._accent is not None
        self._accent = QColor(color) if color else None
        now = self._accent is not None
        if had != now and self._text:
            # Presence of the accent bar changes the reserved text width, so the size
            # must be recomputed to keep measured wrapping == drawn wrapping.
            self.set_text(self._text)
        else:
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Elastic pop: scale the whole shape around the tail tip so it grows out of
        # (and retracts into) the orb/badge instead of just fading.
        g = self._grow.x
        if abs(g - 1.0) > 1e-3:
            anchor = self._tail_tip_local()
            painter.translate(anchor)
            painter.scale(g, g)
            painter.translate(-anchor.x(), -anchor.y())

        o = self._body_origin()
        body = QRectF(o.x(), o.y(), self._body_w, self._body_h)
        path = QPainterPath()
        path.addRoundedRect(body, self.RADIUS, self.RADIUS)

        cx = body.center().x()
        cy = body.center().y()
        tail = QPolygonF()
        if self._tail_side == "bottom":
            y = body.bottom()
            tail.append(QPointF(cx - self.TAIL_W / 2, y))
            tail.append(QPointF(cx + self.TAIL_W / 2, y))
            tail.append(QPointF(cx, y + self.TAIL_H))
        elif self._tail_side == "top":
            y = body.top()
            tail.append(QPointF(cx - self.TAIL_W / 2, y))
            tail.append(QPointF(cx + self.TAIL_W / 2, y))
            tail.append(QPointF(cx, y - self.TAIL_H))
        elif self._tail_side == "left":
            x = body.left()
            tail.append(QPointF(x, cy - self.TAIL_W / 2))
            tail.append(QPointF(x, cy + self.TAIL_W / 2))
            tail.append(QPointF(x - self.TAIL_H, cy))
        else:  # right
            x = body.right()
            tail.append(QPointF(x, cy - self.TAIL_W / 2))
            tail.append(QPointF(x, cy + self.TAIL_W / 2))
            tail.append(QPointF(x + self.TAIL_H, cy))
        tail_path = QPainterPath()
        tail_path.addPolygon(tail)
        path = path.united(tail_path)

        # Manual soft drop shadow (replaces QGraphicsDropShadowEffect, which breaks
        # live-resize repaint on this translucent window). A few stacked translucent
        # copies, offset downward, fake a soft lifted shadow within the SHADOW margin.
        painter.setPen(Qt.PenStyle.NoPen)
        for dy, a in ((5.0, 18), (3.0, 26), (1.5, 34)):
            painter.save()
            painter.translate(0.0, dy)
            painter.setBrush(QColor(0, 0, 0, a))
            painter.drawPath(path)
            painter.restore()

        # Flat fill, no border — a stroked border left a visible seam where the
        # tail meets the body.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(20, 30, 51, 250)))
        painter.drawPath(path)

        # Category/stage accent: a slim rounded bar on the tail side of the body.
        if self._accent is not None:
            painter.setBrush(QBrush(self._accent))
            bar_w = 3.0
            inset = 6.0
            if self._tail_side == "right":
                bx = body.right() - inset - bar_w
            else:
                bx = body.left() + inset
            bar = QRectF(bx, body.top() + inset, bar_w, self._body_h - 2 * inset)
            painter.drawRoundedRect(bar, bar_w / 2, bar_w / 2)

        painter.setPen(QColor("#EAF0FB"))
        painter.setFont(self._font)
        text_rect = QRectF(
            body.x() + self.PAD_X + (self.ACCENT_W if self._tail_side != "right" and self._accent else 0),
            body.y() + self.PAD_Y,
            self._body_w - 2 * self.PAD_X - (self.ACCENT_W if self._accent else 0),
            self._body_h - 2 * self.PAD_Y,
        )
        painter.drawText(
            text_rect,
            int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
        )


class ContextBadge(QWidget):
    """A small circular badge shown below the orb while collapsed, displaying the
    icon of the currently detected app, connected to the orb by a curved
    'telephone-cord' whose color reflects the active polish category. Lets the user
    confirm at a glance which app context was recognised and which style is active.
    Custom-painted (a bare translucent widget won't paint a stylesheet background)."""

    BADGE_D = 32          # diameter of the icon circle
    RING = 2              # colored ring thickness
    CORD_LEN = 34         # vertical span of the coiled cord from orb to badge
    SHADOW = 14           # transparent margin for the drop shadow
    LABEL_H = 15          # room for the app-name label under the badge
    COIL_TURNS = 5        # number of spring loops
    COIL_AMP = 7.0        # half-width of each spring loop
    SWING_ROOM = 28       # extra horizontal margin so the cord can swing widely
    BOB_ROOM = 20         # extra vertical margin for the bungee bounce

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._color = QColor("#6EA8FC")
        self._pixmap: QPixmap | None = None
        self._letter = "?"
        self._label = ""
        self._font = QFont("Segoe UI", 8, QFont.Weight.DemiBold)
        self._pulse_t = -1.0   # 0..1 position of the energy dot on the cord (<0 = off)
        self._ring_glow = 0.0  # 0..1 brightness of the "connected" ring flash

        # --- living-cord animation state (driven by a ~60fps timer while visible) --
        # The badge hangs off the cord and behaves like a real dangling telephone
        # cord: a pendulum swing with inertia, an occasional ambient "breeze", a
        # gently breathing coil, a springy icon pop on app change, and — while the
        # pet is actively capturing — soft energy dots flowing down toward the app.
        self._t0 = time.perf_counter()
        self._last = self._t0
        # Bouncy pendulum + a vertical bungee so the cord reads as genuinely elastic:
        # low omega = long, loose swings; very low zeta = lots of overshoot before it
        # settles, like a real dangling coiled cord.
        self._swing = _Spring(0.0, 6.0, 0.085)   # horizontal dangle offset (px)
        self._stretch = _Spring(0.0, 9.0, 0.11)  # vertical bungee bob (px)
        self._sPop = _Spring(1.0, 20.0, 0.42)    # icon scale (pop-in bounce)
        self._breeze_timer = 1.2
        self._flowing = False
        self._flow_dots: list[float] = []        # positions 0..1 down the cord
        self._flow_spawn = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._tick)

        # NOTE: intentionally NO QGraphicsDropShadowEffect here. On a translucent
        # frameless top-level window that effect can blank the widget's content on
        # activation-driven repaints (the icon "disappears" when switching apps), so
        # we paint a soft shadow manually in paintEvent instead.

        m = self.SHADOW
        w = self.BADGE_D + 2 * m
        h = self.CORD_LEN + self.BADGE_D + self.LABEL_H + 2 * m
        self.resize(w, h)

    def set_context(self, *, color: str, pixmap: QPixmap | None, letter: str, label: str) -> None:
        prev_letter, prev_label = self._letter, self._label
        self._color = QColor(color)
        self._pixmap = pixmap
        self._letter = (letter or "?")[:1].upper()
        self._label = label or ""
        # A new app was recognised → give the icon a cute springy pop-in.
        if self._letter != prev_letter or self._label != prev_label:
            self._sPop.x = 0.55
            self._sPop.v = 0.0
            self._sPop.set(1.0)
            self.nudge(3.0)
        self.update()

    def set_flowing(self, flowing: bool) -> None:
        """While True (actively capturing), soft energy dots flow down the cord."""
        self._flowing = bool(flowing)

    def nudge(self, strength: float = 5.0) -> None:
        """Kick the dangling badge into a gentle swing (e.g. when the pet hops)."""
        self._swing.kick(strength * 6.0)

    def showEvent(self, event) -> None:  # noqa: N802
        self._last = time.perf_counter()
        if not self._anim.isActive():
            self._anim.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._anim.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        # Ambient breeze: an occasional tiny impulse so the cord is never dead-still.
        self._breeze_timer -= dt
        if self._breeze_timer <= 0:
            import random

            self._swing.kick((random.random() - 0.5) * 9.0)
            self._breeze_timer = 2.2 + random.random() * 2.8
        self._swing.step(dt)
        self._sPop.step(dt)
        # Flow dots travelling down the cord toward the app while capturing.
        if self._flowing:
            self._flow_spawn -= dt
            if self._flow_spawn <= 0:
                self._flow_dots.append(0.0)
                self._flow_spawn = 0.5
        self._flow_dots = [d + dt * 1.3 for d in self._flow_dots if d < 1.0]
        self.update()

    def set_pulse(self, t: float) -> None:
        """Position (0..1) of the energy dot travelling down the cord; <0 hides it."""
        self._pulse_t = t
        self.update()

    def set_ring_glow(self, g: float) -> None:
        """Brightness (0..1) of the 'connection established' ring flash."""
        self._ring_glow = max(0.0, min(1.0, g))
        self.update()

    def cord_top_local(self) -> QPointF:
        """Local point where the cord starts (touches the orb bottom)."""
        return QPointF(self.width() / 2, self.SHADOW)

    def _coil_path(
        self, cx: float, y0: float, y1: float, sway: float = 0.0, amp_scale: float = 1.0
    ) -> QPainterPath:
        """A stretched-helix path between y0 and y1 that reads as a coiled spring /
        telephone cord. Modeled as x = amp·sin(θ) with a slight perspective squash so
        successive loops look 3D rather than a flat zig-zag. ``sway`` bends the cord
        horizontally (0 at the fixed top anchor, full at the hanging bottom) and
        ``amp_scale`` breathes the loop width."""
        path = QPainterPath()
        span = max(y1 - y0, 1.0)
        steps = 96
        amp = self.COIL_AMP * amp_scale
        for i in range(steps + 1):
            t = i / steps
            theta = t * self.COIL_TURNS * 2 * math.pi
            # ease the amplitude in/out so the coil tapers into the endpoints
            taper = math.sin(min(t, 1 - t) * math.pi) ** 0.5 if 0 < t < 1 else 0.0
            # sway bends more toward the bottom (eased) — like a hanging cord
            bend = sway * (t * t)
            x = cx + bend + amp * taper * math.sin(theta)
            y = y0 + t * span
            if i == 0:
                path.moveTo(QPointF(x, y))
            else:
                path.lineTo(QPointF(x, y))
        return path

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        m = self.SHADOW
        base_cx = self.width() / 2
        r = self.BADGE_D / 2
        # Live cord dynamics: a horizontal dangle (pendulum + ambient breeze) and a
        # slowly breathing coil width. The cord top is anchored under the orb (fixed
        # x); the badge hangs at the bottom, so it shares the full sway.
        sway = self._swing.x
        tsec = time.perf_counter() - self._t0
        amp_scale = 1.0 + 0.14 * math.sin(tsec * 1.8)
        cx = base_cx + sway
        badge_cy = m + self.CORD_LEN + r
        badge_center = QPointF(cx, badge_cy)

        # Coiled "telephone cord" spring from just under the orb to the badge top.
        coil = self._coil_path(base_cx, m + 2, badge_cy - r - 1, sway=sway, amp_scale=amp_scale)
        # soft under-shadow of the cord for a subtle 3D tube look
        painter.setBrush(Qt.BrushStyle.NoBrush)
        shadow_pen = QPen(QColor(0, 0, 0, 70), 4.0)
        shadow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        shadow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(shadow_pen)
        painter.translate(0.6, 1.0)
        painter.drawPath(coil)
        painter.translate(-0.6, -1.0)
        pen = QPen(self._color, 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(coil)

        # Energy dot travelling down the cord (the "connect to app" moment).
        if 0.0 <= self._pulse_t <= 1.0:
            pt = coil.pointAtPercent(self._pulse_t)
            glow = QColor(self._color)
            glow.setAlpha(70)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(pt, 7.0, 7.0)
            core = QColor("#FFFFFF")
            painter.setBrush(QBrush(core))
            painter.drawEllipse(pt, 2.6, 2.6)

        # Soft energy dots streaming down the cord while actively capturing.
        for d in self._flow_dots:
            fp = coil.pointAtPercent(max(0.0, min(1.0, d)))
            fade = math.sin(max(0.0, min(1.0, d)) * math.pi)
            dot = QColor(self._color)
            dot.setAlpha(int(210 * fade))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(dot))
            painter.drawEllipse(fp, 3.2, 3.2)

        # Springy pop of the whole badge disc when a new app is recognised.
        pop = self._sPop.x
        painter.save()
        painter.translate(badge_center)
        painter.scale(pop, pop)
        painter.translate(-badge_center.x(), -badge_center.y())

        # Colored ring with a TRANSPARENT center (no disc fill) so the desktop shows
        # through behind the app icon. A soft dark ring behind fakes a drop shadow.
        inner = r - self.RING
        painter.setBrush(Qt.BrushStyle.NoBrush)
        shadow_ring = QPen(QColor(0, 0, 0, 60), self.RING + 2)
        painter.setPen(shadow_ring)
        painter.drawEllipse(QPointF(cx, badge_cy + 1.0), r - self.RING / 2, r - self.RING / 2)

        # "Connection established" flash: an expanding, fading halo around the ring.
        if self._ring_glow > 0.0:
            halo = QColor(self._color)
            halo.setAlpha(int(150 * self._ring_glow))
            painter.setPen(QPen(halo, self.RING + 1))
            spread = r + 2 + 6 * (1.0 - self._ring_glow)
            painter.drawEllipse(badge_center, spread, spread)

        ring_pen = QPen(self._color, self.RING)
        painter.setPen(ring_pen)
        painter.drawEllipse(badge_center, r - self.RING / 2, r - self.RING / 2)

        # App icon centered and clipped to the inner circle, or a letter fallback.
        clip = QPainterPath()
        clip.addEllipse(badge_center, inner, inner)
        # Subtle light backing disc so transparent/dark app icons read cleanly.
        backing = QColor(255, 255, 255, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(backing))
        painter.drawEllipse(badge_center, inner, inner)
        if self._pixmap is not None and not self._pixmap.isNull():
            # Draw into a centered square target rect; drawPixmap(target, src) is
            # devicePixelRatio-aware, so HiDPI icons stay centered (previous manual
            # offset used physical px and pushed the icon to a corner).
            inner_d = inner * 2
            target = QRectF(cx - inner, badge_cy - inner, inner_d, inner_d)
            src = QRectF(self._pixmap.rect())
            painter.save()
            painter.setClipPath(clip)
            painter.drawPixmap(target, self._pixmap, src)
            painter.restore()
        else:
            # No icon available: a faint category-tinted disc keeps the letter legible.
            faint = QColor(self._color)
            faint.setAlpha(70)
            painter.save()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(faint))
            painter.drawEllipse(badge_center, inner, inner)
            painter.setPen(QColor("#FFFFFF"))
            f = QFont("Segoe UI", 15, QFont.Weight.Bold)
            painter.setFont(f)
            painter.drawText(
                QRectF(cx - inner, badge_cy - inner, inner * 2, inner * 2),
                int(Qt.AlignmentFlag.AlignCenter),
                self._letter,
            )
            painter.restore()

        painter.restore()  # balance the springy-pop save() at the disc start

        # App-name label under the badge.
        if self._label:
            painter.setPen(QColor("#C7D2E8"))
            painter.setFont(self._font)
            painter.drawText(
                QRectF(0, badge_cy + r + 1, self.width(), self.LABEL_H),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                self._label,
            )


def _apply_button_icon(button, name: str, color: str = ICON_COLOR, size: int = 20) -> None:
    """Set a Font Awesome (qtawesome) icon on a button. Falls back silently if
    qtawesome is unavailable so the UI still works (just without an icon)."""
    try:
        import qtawesome as qta

        button.setIcon(qta.icon(name, color=color))
        button.setIconSize(QSize(size, size))
    except Exception:  # noqa: BLE001
        pass


class RealtimeStreamWorker(QThread):
    """Live streaming transcription via the Azure OpenAI Realtime API.

    Captures microphone audio and pushes 24 kHz PCM to a realtime transcription
    session with server-side VAD, so each spoken phrase is transcribed as the
    user pauses (true streaming, not record-then-send). Emits `partial` with the
    running transcript and `finished_text` with the final transcript."""

    partial = Signal(str)
    finished_text = Signal(str)
    failed = Signal(str)

    def __init__(self, azure_cfg: dict, language_hint: str, prompt: str) -> None:
        super().__init__()
        self._cfg = azure_cfg
        self._language_hint = language_hint
        self._prompt = prompt
        self._stop = threading.Event()
        self._conn = None
        self._mic: sd.RawInputStream | None = None
        self._final_parts: list[str] = []
        self._active_delta = ""
        self._lock = threading.Lock()
        self._pending: list[bytes] = []
        self._ready = False
        self._sent_live = 0
        self._tail_sent = False
        self._dbg_chunks: list[bytes] = []

    def _joined(self) -> str:
        parts = list(self._final_parts)
        if self._active_delta:
            parts.append(self._active_delta)
        return "".join(parts).strip()

    def _start_mic(self) -> None:
        import base64

        debug = bool(os.environ.get("COPILOT_RT_DEBUG"))
        self._cb_count = 0

        def callback(indata, _frames, _time, _status) -> None:
            if debug:
                self._cb_count += 1
                if _status:
                    _rt_log(f"cb status @{self._cb_count}: {_status}")
            if self._stop.is_set():
                return
            chunk = bytes(indata)
            with self._lock:
                # Until the realtime session is ready, buffer captured audio so the
                # very first words (spoken during WebSocket/session setup) are not
                # lost. Once ready, drain the buffer in order, then stream live.
                if not self._ready or self._conn is None:
                    self._pending.append(chunk)
                    if debug:
                        self._dbg_chunks.append(chunk)
                    return
                try:
                    audio_b64 = base64.b64encode(chunk).decode()
                    self._conn.send({"type": "input_audio_buffer.append", "audio": audio_b64})
                    self._sent_live += 1
                    if debug:
                        self._dbg_chunks.append(chunk)
                except Exception as exc:  # noqa: BLE001
                    _rt_log(f"live send error: {type(exc).__name__}: {exc}")

        try:
            self._mic = sd.RawInputStream(
                samplerate=REALTIME_SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=REALTIME_SAMPLE_RATE // 10,  # 100ms blocks
                latency="high",
                callback=callback,
            )
            self._mic.start()
            _rt_log(f"mic started (latency={self._mic.latency})")
        except Exception as exc:  # noqa: BLE001
            _rt_log(f"mic start FAILED: {type(exc).__name__}: {exc}")
            raise

    def _flush_pending(self) -> None:
        """Mark the session ready and send any audio buffered during setup, in
        order, before live audio starts flowing."""
        import base64

        with self._lock:
            self._ready = True
            pending, self._pending = self._pending, []
            if self._conn is not None:
                # Prepend a short block of silence so the server VAD sees a clean
                # silence->speech transition. Without it, buffered audio that starts
                # mid-speech (the user began talking during connection setup) makes
                # the VAD miss the onset and drop the first word(s).
                lead_silence = b"\x00" * (REALTIME_SAMPLE_RATE * 2 // 2)  # 500ms pcm16
                for chunk in [lead_silence, *pending]:
                    try:
                        audio_b64 = base64.b64encode(chunk).decode()
                        self._conn.send(
                            {"type": "input_audio_buffer.append", "audio": audio_b64}
                        )
                    except Exception as exc:  # noqa: BLE001
                        _rt_log(f"flush send error: {type(exc).__name__}: {exc}")
        _rt_log(f"flushed {len(pending)} buffered chunks")

    def run(self) -> None:
        from . import azure_client

        self._sent_live = 0
        try:
            _rt_log("run start")
            # Start capturing immediately (buffered) so no speech is lost while the
            # realtime connection and session are being established.
            self._start_mic()

            client = azure_client.make_realtime_client(self._cfg)
            deployment = self._cfg["transcribe_deployment"]
            _rt_log(f"client made, deployment={deployment}")
            transcription: dict = {"model": deployment}
            if self._language_hint:
                transcription["language"] = self._language_hint
            if self._prompt:
                transcription["prompt"] = self._prompt

            with client.beta.realtime.connect(
                model=deployment, extra_query={"intent": "transcription"}
            ) as conn:
                self._conn = conn
                _rt_log("connected")
                conn.send(
                    {
                        "type": "transcription_session.update",
                        "session": {
                            "input_audio_format": "pcm16",
                            "input_audio_transcription": transcription,
                            "turn_detection": {
                                "type": "server_vad",
                                "silence_duration_ms": 500,
                                "prefix_padding_ms": 300,
                            },
                        },
                    }
                )
                self._flush_pending()
                # If the user already pressed stop during connection setup (common,
                # since setup takes a few seconds and the whole short utterance was
                # buffered), stop() could not send the finalizing silence yet: it is
                # gated on `_ready` so it never lands before the buffered speech.
                # Now that the buffer is flushed, finalize here so the VAD commits.
                if self._stop.is_set():
                    self._send_tail_silence()
                try:
                    for event in conn:
                        etype = getattr(event, "type", "")
                        _rt_log(f"event: {etype}")
                        if etype == "conversation.item.input_audio_transcription.delta":
                            self._active_delta += getattr(event, "delta", "") or ""
                            self.partial.emit(self._joined())
                        elif etype == "conversation.item.input_audio_transcription.completed":
                            done = getattr(event, "transcript", "") or ""
                            if done:
                                self._final_parts.append(done)
                            self._active_delta = ""
                            self.partial.emit(self._joined())
                            # If the user has asked to stop, this completed event is
                            # the transcription of the final committed buffer — we can
                            # finish now instead of waiting on the fallback close.
                            if self._stop.is_set():
                                _rt_log("final completed after stop; closing")
                                break
                        elif etype == "error":
                            err = getattr(event, "error", event)
                            raise RuntimeError(f"Realtime error: {err}")
                except Exception as exc:  # noqa: BLE001
                    # Connection closed (expected on stop) or transient read error.
                    _rt_log(f"reader loop ended: {type(exc).__name__}: {exc}")
            _rt_log(f"finished, live_chunks={self._sent_live}, text={self._joined()!r}")
            self._dump_debug_wav()
            self.finished_text.emit(self._joined())
        except BaseException as exc:  # noqa: BLE001
            _rt_log(f"run FAILED: {type(exc).__name__}: {exc}")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            # Always release the microphone, no matter how run() exits (normal
            # finish, server-closed connection, or exception during setup).
            self._close_mic()

    def _dump_debug_wav(self) -> None:
        """When COPILOT_RT_DEBUG is set, write the exact captured mic PCM (in the
        order it was buffered/streamed) to a WAV next to the log, so the raw audio
        can be inspected/replayed to tell capture problems from transcription ones."""
        if not os.environ.get("COPILOT_RT_DEBUG") or not self._dbg_chunks:
            return
        try:
            import wave

            path = os.path.join(
                os.path.dirname(os.environ["COPILOT_RT_DEBUG"]) or ".", "_rt_capture.wav"
            )
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(REALTIME_SAMPLE_RATE)
                w.writeframes(b"".join(self._dbg_chunks))
            _rt_log(f"wrote capture wav: {path} ({sum(len(c) for c in self._dbg_chunks)} bytes)")
        except Exception as exc:  # noqa: BLE001
            _rt_log(f"wav dump error: {type(exc).__name__}: {exc}")

    def stop(self) -> None:
        """Stop capturing and let the server VAD finalize the last utterance.

        With server_vad turn detection a manual `input_audio_buffer.commit` is
        rejected — the server only commits when it detects trailing silence. If
        the user stops right after speaking (common, since connection setup takes
        a few seconds and the whole utterance is buffered), no trailing silence
        ever reaches the server and the phrase is never transcribed. So we append
        a short block of silence to trigger the VAD's end-of-speech detection.
        """
        self._stop.set()
        self._close_mic()
        # If the session is already flushed/ready, finalize now. Otherwise run()
        # will send the tail silence right after it flushes (it checks _stop).
        # _send_tail_silence is gated on _ready so it can never land before the
        # buffered speech, which would otherwise drop the utterance.
        self._send_tail_silence()
        # Fallback: if the server never emits a final transcription, force the
        # reader loop to unblock so we don't hang. The reader normally breaks
        # itself as soon as the post-commit `completed` event arrives.
        threading.Timer(6.0, self._safe_close).start()

    def _close_mic(self) -> None:
        """Stop and release the microphone stream if it is open (idempotent)."""
        if self._mic is not None:
            try:
                self._mic.stop()
                self._mic.close()
            except Exception:  # noqa: BLE001
                pass
            self._mic = None

    def _send_tail_silence(self) -> None:
        """Append a short block of silence so the server VAD detects end-of-speech
        and commits the buffered utterance for transcription.

        With server_vad turn detection a manual `input_audio_buffer.commit` is
        rejected — the server only commits on detected trailing silence. When the
        user stops right after speaking, no real trailing silence reaches the
        server, so we synthesize it here. Gated on `_ready` so it is only ever
        appended after the buffered speech has been flushed."""
        if self._conn is None or not self._ready or self._tail_sent:
            return
        self._tail_sent = True
        try:
            import base64

            # 700ms > silence_duration_ms (500ms) so the VAD marks end-of-speech.
            tail_silence = b"\x00" * (REALTIME_SAMPLE_RATE * 2 * 7 // 10)
            with self._lock:
                self._conn.send(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(tail_silence).decode(),
                    }
                )
            _rt_log("sent tail silence to finalize")
        except Exception as exc:  # noqa: BLE001
            _rt_log(f"tail silence send error: {type(exc).__name__}: {exc}")

    def _safe_close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass


class PolishWorker(QThread):
    """Polish already-transcribed text off the UI thread."""

    finished_text = Signal(str, str)

    def __init__(
        self,
        raw_text: str,
        polish: str,
        context_file: Path | None,
        session_context: bool,
        language_preference: str,
        polish_engine: str,
        ollama_model: str,
        target_app_name: str | None = None,
        target_app_bundle_id: str | None = None,
        live_context: str = "",
        focus_sub_kind: str = "",
        copilot_session: bool = False,
    ) -> None:
        super().__init__()
        self._raw = raw_text
        self._polish = polish
        self._context_file = context_file
        self._session_context = session_context
        self._language_preference = language_preference
        self._polish_engine = polish_engine
        self._ollama_model = ollama_model
        self._target_app_name = target_app_name
        self._target_app_bundle_id = target_app_bundle_id
        self._live_context = live_context
        self._focus_sub_kind = focus_sub_kind
        self._copilot_session = copilot_session

    def run(self) -> None:
        try:
            polished = polish_text(
                self._raw,
                self._polish,
                self._context_file,
                session_context=self._session_context,
                language_preference=self._language_preference,
                engine=self._polish_engine,
                ollama_model=self._ollama_model,
                target_app_name=self._target_app_name,
                target_app_bundle_id=self._target_app_bundle_id,
                live_context=self._live_context,
                focus_sub_kind=self._focus_sub_kind,
                copilot_session=self._copilot_session,
            )
        except BaseException:  # noqa: BLE001
            polished = self._raw
        self.finished_text.emit(self._raw, polished)


# Settings grouped into collapsible categories. Each field: (key, kind, options).
# The section title and field label come from the i18n catalog:
#   section -> t(f"settings.section.{section_id}")
#   field   -> t(f"settings.field.{key}")  (dots in azure.* keys are kept)
# kind: "text" -> QLineEdit; "combo" -> QComboBox; "toggle" -> QCheckBox;
#       "action" -> QPushButton.
_SETTINGS_CATEGORIES: list[tuple[str, list[tuple[str, str, tuple[str, ...]]]]] = [
    ("general", [
        ("ui_language", "combo", ("auto", "zh", "en")),
        ("language_preference", "combo", ("zh-en", "zh", "en")),
        ("hotkey", "text", ()),
        ("input_device", "text", ()),
        ("start_collapsed", "toggle", ()),
        ("max_record_seconds", "text", ()),
        ("launch_at_startup", "toggle", ()),
    ]),
    ("transcription", [
        ("backend", "combo", ("faster-whisper", "mlx", "azure")),
        ("model", "combo", (
            "tiny", "base", "small", "medium", "large-v3", "large-v3-turbo", "distil-large-v3",
        )),
        ("_download_model", "action", ()),
        ("hf_endpoint", "text", ()),
        ("mlx_model", "text", ()),
    ]),
    ("polish", [
        ("polish", "combo", ("off", "auto", "copilot", "dev", "im", "notes", "email", "browser")),
        ("polish_engine", "combo", ("rules", "ollama", "azure")),
        ("ollama_model", "text", ()),
    ]),
    ("output", [
        ("copy_to_clipboard", "toggle", ()),
        ("paste_to_active_app", "toggle", ()),
        ("submit_to_active_app", "toggle", ()),
    ]),
    ("azure", [
        ("azure.endpoint", "text", ()),
        ("azure.api_version", "text", ()),
        ("azure.auth", "combo", ("aad", "api_key")),
        ("azure.api_key", "text", ()),
        ("azure.transcribe_deployment", "text", ()),
        ("azure.transcribe_mode", "combo", ("batch", "stream", "realtime")),
        ("azure.realtime_api_version", "text", ()),
        ("azure.chat_deployment", "text", ()),
    ]),
]


def _field_label(key: str) -> str:
    """Localized label for a settings field key (special-case the action button)."""
    if key == "_download_model":
        return t("settings.field.download_model")
    return t(f"settings.field.{key}")


def _field_applies(key: str, backend: str, polish_engine: str) -> bool:
    """Whether a settings field is relevant given the current backend / polish engine.
    Local-model fields are hidden when an online (azure) backend is selected, etc."""
    if key in ("model", "hf_endpoint", "_download_model"):
        return backend == "faster-whisper"
    if key == "mlx_model":
        return backend == "mlx"
    if key == "ollama_model":
        return polish_engine == "ollama"
    if key.startswith("polish_prompts.") or key == "_prompts_note":
        # Prompt overrides drive the LLM polish engines (ollama / azure).
        return polish_engine in ("ollama", "azure")
    if key in ("azure.transcribe_deployment", "azure.transcribe_mode", "azure.realtime_api_version"):
        return backend == "azure"
    if key == "azure.chat_deployment":
        return polish_engine == "azure"
    if key.startswith("azure."):
        return backend == "azure" or polish_engine == "azure"
    return True


def _config_get(cfg: dict, dotted_key: str) -> str:
    if "." in dotted_key:
        section, sub = dotted_key.split(".", 1)
        return str((cfg.get(section) or {}).get(sub, ""))
    return str(cfg.get(dotted_key, ""))


def _config_get_bool(cfg: dict, dotted_key: str) -> bool:
    if "." in dotted_key:
        section, sub = dotted_key.split(".", 1)
        value = (cfg.get(section) or {}).get(sub)
    else:
        value = cfg.get(dotted_key)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _polish_defaults() -> dict:
    """Built-in per-mode polish prompts, used as placeholder text in settings."""
    try:
        from .polish import POLISH_PROMPTS

        return POLISH_PROMPTS
    except Exception:
        return {}


class ResizableTextEdit(QTextEdit):
    """A QTextEdit whose height the user can change by dragging its bottom edge.

    A thin grip zone along the bottom shows a vertical-resize cursor; dragging it
    adjusts the widget's fixed height. On release it asks the top-level window to
    re-fit so the surrounding layout/scroll area updates."""

    _GRIP = 8
    _MIN_H = 40
    _MAX_H = 2000

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._resizing = False
        self._press_y = 0
        self._press_h = 0
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def _in_grip(self, y: int) -> bool:
        return y >= self.height() - self._GRIP

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._in_grip(int(event.position().y())):
            self._resizing = True
            self._press_y = int(event.globalPosition().y())
            self._press_h = self.height()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resizing:
            dy = int(event.globalPosition().y()) - self._press_y
            new_h = max(self._MIN_H, min(self._MAX_H, self._press_h + dy))
            self.setFixedHeight(new_h)
            event.accept()
            return
        # Hover feedback: vertical-resize cursor while over the grip zone.
        if self._in_grip(int(event.position().y())):
            self.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._resizing:
            self._resizing = False
            event.accept()
            win = self.window()
            if hasattr(win, "_fit_height"):
                QTimer.singleShot(0, win._fit_height)
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self._resizing:
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        super().leaveEvent(event)


class _Spring:
    """A damped harmonic oscillator (spring) integrated per-frame. Eases ``x``
    toward ``target`` with natural overshoot/settle instead of a linear tween —
    the single biggest contributor to motion feeling organic rather than robotic.
    ``omega`` = stiffness (higher snappier), ``zeta`` = damping (lower bouncier)."""

    __slots__ = ("x", "v", "target", "omega", "zeta")

    def __init__(self, x: float = 0.0, omega: float = 16.0, zeta: float = 0.55) -> None:
        self.x = x
        self.v = 0.0
        self.target = x
        self.omega = omega
        self.zeta = zeta

    def set(self, t: float) -> None:
        self.target = t

    def kick(self, dv: float) -> None:
        self.v += dv

    def step(self, dt: float) -> float:
        dt = min(dt, 0.032)
        f = -2.0 * self.zeta * self.omega * self.v - self.omega * self.omega * (self.x - self.target)
        self.v += f * dt
        self.x += self.v * dt
        return self.x


class PetOrb(QWidget):
    """The desktop pet, custom-painted as a procedural "jelly blob".

    Replaces the old emoji QLabel so we can do true deformation. A single ~60fps
    timer drives spring physics + oscillators; ``paintEvent`` renders the blob,
    face, trailing antenna, ground shadow, coloured glow and particle accents.

    Design contract (approved via the comparison gallery):
      * the body colour is STABLE (identity) — only the glow/accents carry state;
      * motion = meaning: idle only breathes (never shakes); shake is error-only;
      * spring physics, anticipation, secondary motion, ground shadow and particles
        make each state read as a distinct, natural motion.
    Mouse-transparent so drags / click-to-expand pass through to the parent."""

    BODY = QColor(_style.ORB_BODY)
    INK = QColor(_style.ORB_INK)

    # app processing-stage -> one of the 5 visual states
    _VIS = _style.STAGE_VISUAL
    # state -> glow / accent colour (distinct hues; recording red vs error amber)
    _GLOW = {key: QColor(value) for key, value in _style.VISUAL_GLOW.items()}
    DONE = QColor(_style.STAGE_DONE)
    RECORDING = QColor(_style.STAGE_RECORDING)
    DROP = QColor("#57B6FF")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(132, 132)
        # springs
        self._sHopY = _Spring(0.0, 15.0, 0.42)   # vertical jump (px; up = negative)
        self._sMouth = _Spring(0.0, 18.0, 0.7)
        self._sLean = _Spring(0.0, 12.0, 0.55)   # radians
        self._sGlow = _Spring(0.16, 10.0, 0.9)   # glow alpha 0..1
        # runtime state
        self._vis = "idle"
        self._glow_color = QColor(self._GLOW["idle"])
        self._breath_amp = 0.05
        self._breath_period = 2.6
        self._wobble_amp = 0.0
        self._wobble_speed = 0.0
        self._wobble_phase = 0.0
        self._heart_amp = 0.0
        self._heart_period = 0.76
        self._blink = 0.0
        self._blink_timer = 1.4 + 0.001 * (id(self) % 1800)
        self._shake_t = None
        self._antic_t = None
        self._pending_hop = False
        self._gaze = 0.0
        self._gaze_target = 0.0
        self._gaze_timer = 1.6
        self._parts: list[dict] = []
        self._ripple_timer = 0.0
        self._t0 = time.perf_counter()
        self._last = self._t0
        self.set_stage("idle")
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- public API -----------------------------------------------------------
    def set_stage(self, stage: str) -> None:
        vis = self._VIS.get(stage, "idle")
        self._vis = vis
        self._glow_color = QColor(self._GLOW[vis])
        # defaults: calm
        self._breath_amp = 0.05
        self._breath_period = 2.6
        self._wobble_amp = 0.0
        self._wobble_speed = 0.0
        self._heart_amp = 0.0
        self._sMouth.set(0.0)
        self._sLean.set(0.0)
        self._sGlow.set(_style.GLOW_ALPHA_IDLE)
        if vis == "idle":
            self._sGlow.set(_style.GLOW_ALPHA_IDLE)
            self._sMouth.set(0.12)  # gentle resting smile (a flat line looks unhappy)
        elif vis == "recording":
            self._heart_amp = 0.05
            self._breath_amp = 0.02
            self._sGlow.set(_style.GLOW_ALPHA_RECORDING)
            self._sMouth.set(0.14)
        elif vis == "thinking":
            self._breath_amp = 0.02
            self._sGlow.set(_style.GLOW_ALPHA_WORKING_MAX)
            self._sMouth.set(0.08)
            # Whole-body jelly deformation while polishing/transcribing — a gentle,
            # small-amplitude version of the original wobble (the face is drawn at
            # fixed positions, so only the silhouette breathes, not the mouth).
            self._wobble_amp = 0.05
            self._wobble_speed = 3.0
        elif vis == "done":
            self._sGlow.set(_style.GLOW_ALPHA_DONE)
            self._sMouth.set(0.9)
            self.hop()
            QTimer.singleShot(120, self.blink)
            self._sparkle()
        elif vis == "error":
            self._sGlow.set(_style.GLOW_ALPHA_ERROR)
            self._sMouth.set(-0.5)
            self._shake_t = 0.0
            self._sLean.set(0.26)
            QTimer.singleShot(120, lambda: self._sLean.set(0.0))
            self._sweat()

    def hop(self) -> None:
        """Crouch-then-launch jump (with anticipation). Used on record start."""
        self._antic_t = 0.0
        self._pending_hop = True

    def blink(self) -> None:
        self._blink = 1.0

    # -- one-shots ------------------------------------------------------------
    def _sparkle(self) -> None:
        import random

        for _ in range(12):
            a = -math.pi / 2 + (random.random() - 0.5) * 2.2
            sp = 0.7 + random.random()
            self._parts.append(
                {"t": "star", "x": 0.0, "y": -0.05, "vx": math.cos(a) * sp,
                 "vy": math.sin(a) * sp, "life": 1.0, "rot": random.random() * 6}
            )

    def _sweat(self) -> None:
        self._parts.append({"t": "drop", "x": 0.42, "y": -0.36, "vx": 0.2, "vy": 0.7, "life": 1.0})

    # -- per-frame update -----------------------------------------------------
    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        t = now - self._t0
        R = min(self.width(), self.height()) * 0.34
        # springs
        self._sMouth.step(dt)
        self._sLean.step(dt)
        self._sGlow.step(dt)
        # anticipation -> launch
        antic_crouch = 0.0
        if self._antic_t is not None:
            self._antic_t += dt / 0.13
            if self._antic_t >= 1.0:
                self._antic_t = None
                if self._pending_hop:
                    self._sHopY.kick(-5.5 * R)
                    self._pending_hop = False
            else:
                antic_crouch = math.sin(self._antic_t * math.pi) * 0.28 * R
        self._sHopY.step(dt)
        # thinking: a small whole-body jelly deformation (set via _wobble_amp) plus a
        # gentle vertical float + slow breathing aura — reads as "processing". The
        # face is drawn at fixed positions, so only the silhouette flexes.
        think_bob = math.sin(t * 2.3) * 0.025 * R if self._vis == "thinking" else 0.0
        if self._vis == "thinking":
            span = _style.GLOW_ALPHA_WORKING_MAX - _style.GLOW_ALPHA_WORKING_MIN
            self._sGlow.set(_style.GLOW_ALPHA_WORKING_MIN + span * (0.5 + 0.5 * math.sin(t * 2.6)))
        self._off_y = self._sHopY.x + antic_crouch + think_bob
        # squash physically coupled to jump spring: airborne->stretch, land->squash
        squash = max(-0.12, min(0.18, self._sHopY.x / max(1.0, R) * 0.5))
        # oscillators
        self._wobble_phase += self._wobble_speed * dt
        squash += math.sin(t / self._breath_period * math.pi * 2) * self._breath_amp
        if self._heart_amp > 0:
            squash += (max(0.0, math.sin(t / self._heart_period * math.pi * 2)) ** 3) * self._heart_amp
        self._squash = squash
        # idle sway + look-around
        sway = math.sin(t / 1.7) * 0.04 * R if self._vis == "idle" else 0.0
        self._gaze_timer -= dt
        if self._gaze_timer <= 0:
            import random

            self._gaze_target = (random.random() - 0.5) * 1.4
            self._gaze_timer = 1.4 + random.random() * 2.4
            if random.random() < 0.3:
                self.blink()
        self._gaze += (self._gaze_target - self._gaze) * 0.06
        # blink
        self._blink_timer -= dt
        if self._blink_timer <= 0:
            import random

            self.blink()
            self._blink_timer = 2.2 + random.random() * 2.6
        if self._blink > 0:
            self._blink = max(0.0, self._blink - dt / 0.11)
        # shake (decaying)
        off_x = sway
        if self._shake_t is not None:
            self._shake_t += dt / 0.62
            if self._shake_t >= 1.0:
                self._shake_t = None
            else:
                off_x += math.sin(self._shake_t * math.pi * 6) * 0.42 * R * (1.0 - self._shake_t)
        self._off_x = off_x
        # particles
        self._update_parts(dt)
        self.update()

    def _update_parts(self, dt: float) -> None:
        if self._vis == "recording":
            self._ripple_timer -= dt
            if self._ripple_timer <= 0:
                self._parts.append({"t": "ring", "r": 0.0, "life": 1.0})
                self._ripple_timer = 0.62
        for p in self._parts:
            if p["t"] == "ring":
                p["r"] += dt * 0.9
                p["life"] -= dt * 1.5
            elif p["t"] == "star":
                p["x"] += p["vx"] * dt
                p["y"] += p["vy"] * dt
                p["vy"] += 2.0 * dt
                p["life"] -= dt * 1.1
                p["rot"] += dt * 6
            elif p["t"] == "drop":
                p["x"] += p["vx"] * dt
                p["y"] += p["vy"] * dt
                p["vy"] += 2.0 * dt
                p["life"] -= dt * 0.7
        self._parts = [p for p in self._parts if p["life"] > 0]

    # -- painting -------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        R = min(w, h) * 0.34
        rest_cy = h * 0.50
        cx = w / 2 + getattr(self, "_off_x", 0.0)
        cy = rest_cy + getattr(self, "_off_y", 0.0)
        squash = getattr(self, "_squash", 0.0)
        # ground shadow (reacts to height & squash)
        height = max(0.0, min(1.0, -getattr(self, "_off_y", 0.0) / (2.0 * R)))
        sw = R * (1.05 + squash * 1.4) * (1 - height * 0.35)
        p.setPen(Qt.PenStyle.NoPen)
        sh = QColor("#2A3556")
        sh.setAlphaF(0.20 * (1 - height * 0.5))
        p.setBrush(sh)
        p.drawEllipse(QPointF(w / 2, rest_cy + R * 0.98), sw, R * 0.20 * (1 - height * 0.3))
        # glow
        outer = R * 1.8
        grad = QRadialGradient(cx, cy, outer)
        gc0 = QColor(self._glow_color)
        gc0.setAlphaF(max(0.0, min(1.0, self._sGlow.x)))
        gc1 = QColor(self._glow_color)
        gc1.setAlpha(0)
        grad.setColorAt(0.0, gc0)
        grad.setColorAt(1.0, gc1)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(cx, cy), outer, outer)
        # recording ripples
        for pt in self._parts:
            if pt["t"] != "ring":
                continue
            rc = QColor(self.RECORDING)
            rc.setAlphaF(0.5 * max(0.0, pt["life"]))
            pen = QPen(rc, 3)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), R * (0.9 + pt["r"]), R * (0.9 + pt["r"]))
        p.setPen(Qt.PenStyle.NoPen)
        # body (transformed: translate/rotate/scale for lean + area-conserving squash)
        sx = 1 + squash
        sy = 1 / (1 + squash)
        p.save()
        p.translate(cx, cy)
        p.rotate(self._sLean.x * 0.5 * 180.0 / math.pi)
        p.scale(sx, sy)
        path = self._blob_path(R)
        p.setBrush(self.BODY)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)
        # face
        self._draw_face(p, R)
        # body-space particles (stars / drop)
        for pt in self._parts:
            if pt["t"] == "star":
                p.save()
                p.translate(pt["x"] * R, pt["y"] * R)
                p.rotate(pt["rot"] * 180.0 / math.pi)
                sc = QColor(self.DONE)
                sc.setAlphaF(max(0.0, min(1.0, pt["life"])))
                p.setBrush(sc)
                p.setPen(Qt.PenStyle.NoPen)
                self._draw_star(p, R * 0.12)
                p.restore()
            elif pt["t"] == "drop":
                p.save()
                p.translate(pt["x"] * R, pt["y"] * R)
                dc = QColor(self.DROP)
                dc.setAlphaF(max(0.0, min(1.0, pt["life"])))
                p.setBrush(dc)
                p.setPen(Qt.PenStyle.NoPen)
                self._draw_drop(p, R * 0.12)
                p.restore()
        p.restore()
        p.end()

    def _blob_path(self, R: float) -> QPainterPath:
        n = 48
        pts = []
        for i in range(n):
            a = i / n * math.pi * 2
            wob = self._wobble_amp * (
                math.sin(3 * a + self._wobble_phase) * 0.6
                + math.sin(5 * a - self._wobble_phase * 1.3) * 0.4
            )
            rr = R * (1 + wob)
            pts.append((math.cos(a) * rr, math.sin(a) * rr))

        def mid(p, q):
            return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)

        path = QPainterPath()
        m0 = mid(pts[n - 1], pts[0])
        path.moveTo(m0[0], m0[1])
        for i in range(n):
            p1 = pts[i]
            p2 = pts[(i + 1) % n]
            mp = mid(p1, p2)
            path.quadTo(p1[0], p1[1], mp[0], mp[1])
        path.closeSubpath()
        return path

    def _draw_face(self, p: QPainter, R: float) -> None:
        eye_y = -R * 0.02
        eye_x = R * 0.29
        # Simple round dot eyes (no catchlight/gloss); blink squashes them to a line.
        # Kept small for a cleaner, more iconic read (smaller 五官 = higher recognisability).
        ew = R * 0.058
        eh = ew * (1 - self._blink) + R * 0.012
        gx = self._gaze * R * 0.05
        for sgn in (-1, 1):
            p.setBrush(self.INK)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(sgn * eye_x + gx, eye_y), ew, eh)
        # mouth — a single continuous black stroke. The curve's control point moves
        # smoothly with `m` (>0 smile / 0 flat / <0 frown), so a value hovering near
        # zero renders as a near-flat mouth instead of hard-flipping between a smile
        # and a frown shape (which read as an unsettling crying/smiling flicker while
        # the underdamped mouth spring settles).
        m = max(-1.0, min(1.0, self._sMouth.x))
        my = R * 0.24
        half_w = R * 0.11
        ctrl_y = my + m * R * 0.22  # +y is downward: m>0 dips down (smile), m<0 lifts (frown)
        pen = QPen(self.INK, R * 0.045, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        path.moveTo(-half_w, my)
        path.quadTo(0, ctrl_y, half_w, my)
        p.drawPath(path)

    def _draw_star(self, p: QPainter, r: float) -> None:
        path = QPainterPath()
        for i in range(8):
            a = i / 8 * math.pi * 2
            rad = r if i % 2 == 0 else r * 0.42
            x, y = math.cos(a) * rad, math.sin(a) * rad
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        p.drawPath(path)

    def _draw_drop(self, p: QPainter, r: float) -> None:
        path = QPainterPath()
        path.moveTo(0, -r)
        path.quadTo(r * 0.9, r * 0.2, 0, r)
        path.quadTo(-r * 0.9, r * 0.2, 0, -r)
        path.closeSubpath()
        p.drawPath(path)


class SignInWorker(QThread):
    """Runs the (blocking) interactive Azure sign-in off the UI thread so the
    browser round-trip never freezes the overlay."""

    signed_in = Signal(dict)
    failed = Signal(str)

    def run(self) -> None:
        try:
            from . import azure_client

            status = azure_client.sign_in()
            self.signed_in.emit(status)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class AuthStatusWorker(QThread):
    """Probes Azure auth status off the UI thread (the check may mint a cached
    token). Results are delivered via a queued signal so the UI update runs on
    the Qt thread — a plain thread + QTimer.singleShot would never fire because
    the worker thread has no event loop."""

    ready = Signal(dict)

    def run(self) -> None:
        try:
            from . import azure_client

            status = azure_client.auth_status()
        except Exception:  # noqa: BLE001
            status = {"signed_in": True}  # fail open: don't nag on odd errors
        self.ready.emit(status)


class LiveContextWorker(QThread):
    """Runs the (potentially slow) UIA deep-enrich off the UI thread so the LIVE
    'Active Context' panel can show terminal/editor/chat details and detect a
    focused Copilot CLI pane *before* recording — without stalling the 500ms
    focus poller. Results are delivered via a queued signal (see AuthStatusWorker
    for why a plain thread + QTimer would never fire)."""

    ready = Signal("qlonglong", object)  # (hwnd, FocusInfo); qlonglong avoids HWND truncation

    def __init__(self, system: str, hwnd: int, exe_path: str, name: str) -> None:
        super().__init__()
        self._system = system
        self._hwnd = hwnd
        self._exe_path = exe_path
        self._name = name

    def run(self) -> None:
        try:
            info = focus_context.enrich(
                self._system, self._hwnd, self._exe_path, self._name
            )
        except BaseException:
            return
        self.ready.emit(self._hwnd, info)


class ModelDownloadWorker(QThread):
    """Downloads a faster-whisper model into the local HF cache off the UI thread.
    Only meaningful in a build that bundles the local Whisper stack (the lean
    Azure-only build reports a friendly message instead)."""

    done = Signal(str, str)  # (model_name, cache_path)
    failed = Signal(str)

    def __init__(self, model_name: str, hf_endpoint: str) -> None:
        super().__init__()
        self.model_name = model_name
        self.hf_endpoint = hf_endpoint

    def run(self) -> None:
        try:
            from .cli import predownload_model

            path = predownload_model(self.model_name, self.hf_endpoint)
            self.done.emit(self.model_name, str(path))
        except ImportError:
            from .i18n import t

            self.failed.emit(t("msg.model_no_local_engine"))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class VoiceDesktop(QWidget):
    hotkey_pressed = Signal()

    def __init__(
        self,
        *,
        hotkey: str,
        language: str,
        model_name: str,
        backend: str,
        mlx_model: str,
        paste_to_active_app: bool | None,
        submit_to_active_app: bool | None,
        copy_to_clipboard: bool | None = None,
        hf_endpoint: str,
        replacement_pairs: list[str],
        replacements_file: Path | None,
        polish: str,
        context_file: Path | None,
        session_context: bool,
        language_preference: str,
        polish_engine: str,
        ollama_model: str,
    ) -> None:
        super().__init__()
        self.hotkey = hotkey
        self.language = language
        self.model_name = model_name
        self.backend = backend
        self.mlx_model = mlx_model
        # Delivery flags: an explicit CLI flag (--paste/--no-paste, etc.) wins; when
        # left unset (None) the persisted setting from the settings panel is used, so
        # toggling "复制到剪贴板" alone in settings is honored on the next launch too.
        _boot_cfg = _config.load_config()

        def _resolve(flag: bool | None, key: str) -> bool:
            return bool(flag) if flag is not None else _config_get_bool(_boot_cfg, key)

        self.copy_to_clipboard = _resolve(copy_to_clipboard, "copy_to_clipboard")
        self.paste_to_active_app = _resolve(paste_to_active_app, "paste_to_active_app")
        self.submit_to_active_app = _resolve(submit_to_active_app, "submit_to_active_app")
        self.hf_endpoint = hf_endpoint
        self.replacement_pairs = replacement_pairs
        self.replacements_file = replacements_file
        self.polish = polish
        self.context_file = context_file
        self.session_context = session_context
        self.language_preference = language_preference
        self.polish_engine = polish_engine
        self.ollama_model = ollama_model
        self.recorder = AudioRecorder()
        self.stream_worker: RealtimeStreamWorker | None = None
        self.worker: TranscribeWorker | None = None
        # Background transcribe/polish jobs run concurrently: a new recording can
        # start while previous ones are still transcribing/polishing. Keep strong
        # references here so QThreads aren't garbage-collected mid-run, and drop
        # each one when it finishes.
        self._active_workers: set[QThread] = set()
        self.hotkey_listener: keyboard.GlobalHotKeys | None = None
        self._hotkey_timer: QTimer | None = None
        self._hotkey_watch_last: float = 0.0
        self._topmost_timer: QTimer | None = None
        self._focus_timer: QTimer | None = None
        self._token_timer: QTimer | None = None
        self._preferred_target: FocusTarget | None = None
        self._recording_target: FocusTarget | None = None
        self._light_session_title: str = ""  # last title we ran a live session probe for
        self._live_ctx_worker: "LiveContextWorker | None" = None
        self._live_ctx_key: tuple = ()  # (hwnd, title) last deep-enriched live
        self._live_ctx_ts: float = 0.0  # monotonic time of last live deep-enrich
        self._live_transcript_ts: float = 0.0  # monotonic time of last cheap transcript refresh

        if self.backend == "azure" or self.polish_engine == "azure":
            import threading

            from . import azure_client

            threading.Thread(target=azure_client.warmup, daemon=True).start()

        self.setWindowTitle(t("window.title"))
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(360)
        self.move(80, 80)
        self._drag_offset = None
        self._moved = False
        self._collapsed = False
        self._settings_open = False
        self._stage = "idle"
        self._orb_radius = 66
        # Edge-drag resize state: once the user manually resizes the expanded panel,
        # `_user_size` is remembered so auto-fit stops fighting the chosen size.
        self._resize_margin = 14
        self._user_size = None
        self._programmatic = False
        self._transitioning = False
        self.setMouseTracking(True)

        # The pet: a custom-painted, spring-animated jelly blob (see PetOrb). It owns
        # all of its own motion/colour; VoiceDesktop only tells it the current stage.
        self.orb = PetOrb()
        self.orb.setFixedSize(132, 132)

        # Inline "active app" indicator shown next to the pet in the expanded card,
        # so the current app/context is visible without collapsing to the badge.
        self.app_icon_label = QLabel()
        self.app_icon_label.setFixedSize(20, 20)
        self.app_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.app_name_label = QLabel("")
        self.app_name_label.setObjectName("appName")
        app_row = QHBoxLayout()
        app_row.setContentsMargins(0, 0, 0, 0)
        app_row.setSpacing(6)
        app_row.addStretch(1)
        app_row.addWidget(self.app_icon_label)
        app_row.addWidget(self.app_name_label)
        app_row.addStretch(1)
        self.app_indicator = QWidget()
        self.app_indicator.setLayout(app_row)

        self.status = QLabel("IDLE")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setObjectName("status")
        self.status.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))

        self.tip = QLabel(t("label.hotkey", hotkey=hotkey))
        self.tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tip.setObjectName("tip")

        self.start_button = QPushButton()
        self.start_button.setObjectName("iconbtn")
        self.start_button.setToolTip(t("btn.start.tip"))
        self.stop_button = QPushButton()
        self.stop_button.setObjectName("iconbtn")
        self.stop_button.setToolTip(t("btn.stop.tip"))
        self.shrink_button = QPushButton()
        self.shrink_button.setObjectName("iconbtn")
        self.shrink_button.setToolTip(t("btn.shrink.tip"))
        self.quit_button = QPushButton()
        self.quit_button.setObjectName("iconbtn")
        self.quit_button.setToolTip(t("btn.quit.tip"))
        self.relaunch_button = QPushButton()
        self.relaunch_button.setObjectName("iconbtn")
        self.relaunch_button.setToolTip(t("btn.relaunch.tip"))
        _apply_button_icon(self.start_button, "fa6s.microphone")
        _apply_button_icon(self.stop_button, "fa6s.stop")
        _apply_button_icon(self.shrink_button, "fa6s.compress")
        _apply_button_icon(self.quit_button, "fa6s.xmark")
        _apply_button_icon(self.relaunch_button, "fa6s.rotate")

        top_buttons = QHBoxLayout()
        top_buttons.setSpacing(10)
        top_buttons.addStretch(1)
        top_buttons.addWidget(self.start_button)
        top_buttons.addWidget(self.stop_button)
        top_buttons.addWidget(self.shrink_button)
        top_buttons.addWidget(self.relaunch_button)
        top_buttons.addWidget(self.quit_button)
        top_buttons.addStretch(1)

        self._raw_title = QLabel(t("label.raw_transcript"))
        self._raw_title.setObjectName("section")
        self.copy_raw_button = QPushButton("⧉")
        self.copy_raw_button.setObjectName("copy")
        self.copy_raw_button.setToolTip(t("btn.copy_raw.tip"))
        raw_header = QHBoxLayout()
        raw_header.setContentsMargins(0, 0, 0, 0)
        raw_header.addWidget(self._raw_title)
        raw_header.addStretch(1)
        raw_header.addWidget(self.copy_raw_button)
        self.transcript = ResizableTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText(t("ph.transcript"))
        self.transcript.setFixedHeight(70)

        self._context_title = QLabel(t("label.active_context"))
        self._context_title.setObjectName("section")
        self.context_badge_dot = QLabel("●")
        self.context_badge_dot.setObjectName("contextDot")
        context_header = QHBoxLayout()
        context_header.setContentsMargins(0, 0, 0, 0)
        context_header.addWidget(self._context_title)
        context_header.addStretch(1)
        context_header.addWidget(self.context_badge_dot)
        self.context_view = ResizableTextEdit()
        self.context_view.setReadOnly(True)
        self.context_view.setObjectName("contextView")
        self.context_view.setPlaceholderText(t("ph.context"))
        self.context_view.setFixedHeight(60)

        self._polished_title = QLabel(t("label.polished"))
        self._polished_title.setObjectName("section")
        self.copy_polished_button = QPushButton("⧉")
        self.copy_polished_button.setObjectName("copy")
        self.copy_polished_button.setToolTip(t("btn.copy_polished.tip"))
        polished_header = QHBoxLayout()
        polished_header.setContentsMargins(0, 0, 0, 0)
        polished_header.addWidget(self._polished_title)
        polished_header.addStretch(1)
        polished_header.addWidget(self.copy_polished_button)
        self.polished = ResizableTextEdit()
        self.polished.setReadOnly(True)
        self.polished.setPlaceholderText(t("ph.polished"))
        self.polished.setFixedHeight(70)

        self.error = QLabel(t("status.ready"))
        self.error.setObjectName("error")
        self.error.setWordWrap(True)

        # Azure sign-in affordance: a prominent banner shown above the pet whenever
        # the app is not signed in (only meaningful for the aad backend). It lives in
        # `card_layout` (not the scrollable body), so it stays visible and reachable
        # in BOTH the collapsed and expanded states and never overflows off-screen.
        # Clicking it opens the browser sign-in once; the session is then persisted
        # so future launches are silent.
        self.signin_btn = QPushButton(t("btn.signin"))
        self.signin_btn.setObjectName("signinBanner")
        self.signin_btn.setToolTip(t("btn.signin.tip"))
        self.signin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.signin_btn.hide()

        self.context_section = QWidget()
        _context_layout = QVBoxLayout(self.context_section)
        _context_layout.setContentsMargins(0, 0, 0, 0)
        _context_layout.setSpacing(4)
        _context_layout.addLayout(context_header)
        _context_layout.addWidget(self.context_view)

        self.polished_section = QWidget()
        _polished_layout = QVBoxLayout(self.polished_section)
        _polished_layout.setContentsMargins(0, 0, 0, 0)
        _polished_layout.setSpacing(4)
        _polished_layout.addLayout(polished_header)
        _polished_layout.addWidget(self.polished)

        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(4)
        details_layout.addLayout(raw_header)
        details_layout.addWidget(self.transcript)
        details_layout.addWidget(self.context_section)
        details_layout.addWidget(self.polished_section)
        details_layout.addWidget(self.error)

        self.settings_toggle = QPushButton(f"{t('toggle.settings')}  ▸")
        self.settings_toggle.setObjectName("settingsToggle")
        self.settings_panel = self._build_settings_panel()
        self.settings_panel.hide()

        # Collapsible history of completed dictations. Because transcribe/polish now
        # run concurrently, each finished result is appended here so a new recording
        # never discards a previous one. Collapsed by default; expanded on demand.
        self._history: list[dict] = []
        self.history_toggle = QPushButton(f"{t('toggle.history')}  ▸")
        self.history_toggle.setObjectName("settingsToggle")
        self.history_panel = QWidget()
        self.history_panel.setObjectName("historyPanel")
        self._history_layout = QVBoxLayout(self.history_panel)
        self._history_layout.setContentsMargins(4, 4, 4, 4)
        self._history_layout.setSpacing(4)
        self._history_empty = QLabel(t("label.history_empty"))
        self._history_empty.setObjectName("promptNote")
        self._history_empty.setWordWrap(True)
        self._history_layout.addWidget(self._history_empty)
        self.history_panel.hide()
        self._history_open = False

        # Everything under the orb goes into a scroll area so the card never grows
        # taller than the screen — when settings/prompts expand, the body scrolls.
        self.body = QWidget()
        self.body.setObjectName("body")
        body_layout = QVBoxLayout(self.body)
        self._body_layout = body_layout
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        body_layout.addWidget(self.status)
        body_layout.addWidget(self.tip)
        body_layout.addLayout(top_buttons)
        body_layout.addWidget(self.details)
        body_layout.addWidget(self.history_toggle)
        body_layout.addWidget(self.history_panel)
        body_layout.addWidget(self.settings_toggle)
        body_layout.addWidget(self.settings_panel)
        # Anchor content to the top: when the window is dragged taller, the extra
        # space is absorbed by this stretch instead of spreading between widgets.
        body_layout.addStretch(1)

        self.body_scroll = QScrollArea()
        self.body_scroll.setObjectName("bodyScroll")
        self.body_scroll.setWidget(self.body)
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.body_scroll.viewport().setAutoFillBackground(False)
        self.body.setAutoFillBackground(False)
        self.body_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(8)
        # Sign-in banner sits at the very top of the card — above the pet — so a
        # blocked user always sees it, whether collapsed or expanded.
        card_layout.addWidget(self.signin_btn)
        card_layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.app_indicator)
        card_layout.addWidget(self.body_scroll)

        self.card = QWidget()
        self.card.setObjectName("card")
        self.card.setLayout(card_layout)

        outer = QVBoxLayout()
        outer.setContentsMargins(10, 10, 10, 10)
        outer.addWidget(self.card)
        self.setLayout(outer)
        self.setStyleSheet(self._stylesheet())

        self.start_button.clicked.connect(self.start_recording)
        self.stop_button.clicked.connect(self.stop_recording)
        self.shrink_button.clicked.connect(self.toggle_shrink)
        self.settings_toggle.clicked.connect(self.toggle_settings)
        self.history_toggle.clicked.connect(self.toggle_history)
        self.copy_raw_button.clicked.connect(lambda: self._copy_field(self.transcript, t("label.raw_transcript")))
        self.copy_polished_button.clicked.connect(lambda: self._copy_field(self.polished, t("label.polished")))
        self.quit_button.clicked.connect(self.close)
        self.relaunch_button.clicked.connect(self._relaunch)
        self.signin_btn.clicked.connect(self._start_sign_in)
        self.hotkey_pressed.connect(self.toggle_recording)
        self._max_record_timer = QTimer(self)
        self._max_record_timer.setSingleShot(True)
        self._max_record_timer.timeout.connect(self._on_max_record_timeout)
        self._build_bubble()
        self._set_stage("idle")
        self._install_topmost_guard()
        # Active Context + Polished sections are only meaningful when polishing is
        # enabled; hide them when polish is off to keep the panel uncluttered.
        self._apply_polish_visibility()
        self._signin_worker: "SignInWorker | None" = None
        self._model_worker: "ModelDownloadWorker | None" = None
        self._auth_worker: "AuthStatusWorker | None" = None
        # Surface auth state early so the user can sign in before the first
        # recording instead of hitting an error mid-dictation.
        if self.backend == "azure" or self.polish_engine == "azure":
            QTimer.singleShot(400, self._check_auth_async)

    def _build_bubble(self) -> None:
        """A speech bubble shown near the orb while collapsed. It surfaces the live
        raw transcript, then the polished text, and auto-dismisses after a while so
        it never lingers on screen. Uses a custom-painted bubble with a tail that
        points at the orb and sizes correctly on first render."""
        self._bubble = SpeechBubble(self)
        self._bubble.hide()
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(lambda: self._bubble.pop_out())
        # A second bubble anchored to the context badge, surfacing the collected
        # app context (window title / focus area / current Copilot session) so the
        # user sees "what the app side picked up" next to the app badge.
        self._context_bubble = SpeechBubble(self)
        self._context_bubble.hide()
        self._badge = ContextBadge(self)
        self._badge.hide()
        # The badge lives independently of the bubble so a long utterance keeps the
        # cord on screen until the polished text is backfilled.
        self._badge_timer = QTimer(self)
        self._badge_timer.setSingleShot(True)
        self._badge_timer.timeout.connect(self._hide_badge)
        # Occasional idle blink so the pet feels alive.
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(4800)
        self._blink_timer.timeout.connect(self._blink_orb)
        self._blink_timer.start()

    # ---- entrance / exit animations (avoid abrupt show/hide) --------------- #

    def _stop_anim(self, widget) -> None:
        for attr in ("_show_anim", "_hide_anim"):
            anim = getattr(widget, attr, None)
            if anim is not None:
                try:
                    anim.stop()
                except BaseException:
                    pass
                setattr(widget, attr, None)

    def _pop_in(self, widget, dy: int = 12) -> None:
        """Fade + bounce a (already-positioned) top-level bubble/badge into view."""
        self._stop_anim(widget)
        final = widget.pos()
        start = QPoint(final.x(), final.y() + dy)
        widget.setWindowOpacity(0.0)
        widget.move(start)
        widget.show()
        widget.raise_()
        fade = QPropertyAnimation(widget, b"windowOpacity", widget)
        fade.setDuration(160)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        move = QPropertyAnimation(widget, b"pos", widget)
        move.setDuration(280)
        move.setStartValue(start)
        move.setEndValue(final)
        move.setEasingCurve(QEasingCurve.Type.OutBack)
        group = QParallelAnimationGroup(widget)
        group.addAnimation(fade)
        group.addAnimation(move)
        widget._show_anim = group
        group.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _fade_out(self, widget) -> None:
        """Fade a widget out, then hide it (no abrupt disappearance)."""
        if not widget.isVisible():
            return
        self._stop_anim(widget)
        fade = QPropertyAnimation(widget, b"windowOpacity", widget)
        fade.setDuration(150)
        fade.setStartValue(widget.windowOpacity() or 1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InCubic)

        def _done() -> None:
            if widget.windowOpacity() <= 0.05:
                widget.hide()
                widget.setWindowOpacity(1.0)

        fade.finished.connect(_done)
        widget._hide_anim = fade
        fade.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _reveal(self, widget, *, animate: bool) -> None:
        """Show ``widget`` — with a pop-in when it was hidden, else just ensure it is
        fully opaque and raised (cancelling any in-flight fade)."""
        if animate:
            self._pop_in(widget)
        else:
            self._stop_anim(widget)
            widget.setWindowOpacity(1.0)
            widget.show()
            widget.raise_()

    def _bounce_orb(self) -> None:
        """Trigger the pet's spring hop (anticipation crouch -> launch -> settle) to
        signal recording started. PetOrb owns the motion, so this is just a nudge."""
        self.orb.hop()

    def _blink_orb(self) -> None:
        """Occasional idle blink so the pet feels alive (only when idle)."""
        if self._stage != "idle":
            return
        self.orb.blink()

    def _pulse_cord(self) -> None:
        """Send a glowing energy dot down the cord to the app badge, then flash the
        badge ring — the 'pet connects to the app' moment."""
        if not self._badge.isVisible():
            return
        travel = QVariantAnimation(self)
        travel.setStartValue(0.0)
        travel.setEndValue(1.0)
        travel.setDuration(560)
        travel.setEasingCurve(QEasingCurve.Type.InOutSine)
        travel.valueChanged.connect(lambda v: self._badge.set_pulse(float(v)))
        travel.finished.connect(lambda: self._badge.set_pulse(-1.0))
        flash = QVariantAnimation(self)
        flash.setStartValue(1.0)
        flash.setEndValue(0.0)
        flash.setDuration(460)
        flash.setEasingCurve(QEasingCurve.Type.OutCubic)
        flash.valueChanged.connect(lambda v: self._badge.set_ring_glow(float(v)))
        seq = QSequentialAnimationGroup(self)
        seq.addAnimation(travel)
        seq.addAnimation(flash)
        seq.finished.connect(lambda: self._badge.set_ring_glow(0.0))
        self._cord_pulse_anim = seq
        seq.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _show_bubble(self, text: str, *, final: bool = False) -> None:
        """Show/update the orb bubble with ``text``. While still transcribing
        (``final=False``) it stays up longer; the final polished text lingers a few
        seconds before dismissing. Only shown when collapsed to the orb."""
        spec = make_bubble(
            text,
            kind=BubbleKind.SPEECH,
            stage=self._stage,
            duration_ms=9000 if final else 20000,
        )
        if not spec.text or not self._collapsed:
            return
        was_hidden = not self._bubble.isVisible()
        self._bubble.set_text(spec.text)
        self._bubble.set_accent(spec.accent)
        self._position_bubble()
        if was_hidden:
            self._bubble.pop_in()
        else:
            self._bubble.ensure_shown()
        self._bubble_timer.stop()
        self._bubble_timer.start(spec.duration_ms)

    def _position_bubble(self) -> None:
        """Anchor the bubble to the right of the orb with its tail pointing left at
        the pet. Falls back to the left side if there isn't room on the right."""
        orb_tl = self.orb.mapToGlobal(self.orb.rect().topLeft())
        orb_w = self.orb.width()
        orb_h = self.orb.height()
        orb_right = orb_tl.x() + orb_w
        orb_left = orb_tl.x()
        orb_center_y = orb_tl.y() + orb_h // 2
        gap = -2  # slight overlap so the tail tip visually touches the orb

        bw = self._bubble.width()
        bh = self._bubble.height()
        m = SpeechBubble.SHADOW

        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None

        # Prefer the right of the orb (tail on left). Flip left if not enough room.
        tail_side = "left"
        x = orb_right + gap - m
        if avail is not None and x + bw > avail.right() - 4:
            tail_side = "right"
            x = orb_left - bw - gap + m
        self._bubble.set_tail_side(tail_side)
        bw = self._bubble.width()  # width may change with tail orientation
        bh = self._bubble.height()

        # Vertically center the bubble body on the orb (body center sits at m + body_h/2).
        y = orb_center_y - (m + self._bubble._body_h // 2)
        if avail is not None:
            y = max(avail.top() + 4, min(y, avail.bottom() - bh - 4))
            if tail_side == "left":
                x = orb_right + gap - m
            else:
                x = orb_left - bw - gap + m
            x = max(avail.left() + 4, min(x, avail.right() - bw - 4))
        self._bubble.move(int(x), int(y))

    def _hide_bubble(self) -> None:
        self._bubble_timer.stop()
        self._bubble.pop_out()
        self._hide_badge()

    def _maybe_show_greeting(self) -> None:
        """On the very first launch, pop a one-time friendly bubble introducing BB
        and the activation hotkey, then persist a flag so it never shows again."""
        cfg = _config.load_config()
        if _config_get_bool(cfg, "first_launch_done"):
            return
        # The greeting only makes sense on the collapsed orb. If the user is already
        # interacting with the expanded panel, retry shortly (bounded) rather than
        # burning the one-time flag without ever showing the bubble.
        if not self._collapsed:
            retries = getattr(self, "_greeting_retries", 0)
            if retries < 20:
                self._greeting_retries = retries + 1
                QTimer.singleShot(1500, self._maybe_show_greeting)
            return
        self._show_greeting()
        # Persist only after the bubble is actually shown so it can't be silently
        # consumed; worst case (a crash right after) it simply shows once more.
        try:
            _config.save_config({"first_launch_done": True})
        except Exception:  # noqa: BLE001
            pass

    def _show_greeting(self) -> None:
        """Show the welcome bubble near the orb (only meaningful while collapsed)."""
        if not self._collapsed:
            return
        spec = make_bubble(
            t("bubble.greeting", hotkey=str(self.hotkey).upper()),
            kind=BubbleKind.GREETING,
            accent="#6EA8FC",
        )
        was_hidden = not self._bubble.isVisible()
        self._bubble.set_text(spec.text)
        self._bubble.set_accent(spec.accent)
        self._position_bubble()
        if was_hidden:
            self._bubble.pop_in()
        else:
            self._bubble.ensure_shown()
        self._bounce_orb()
        self._bubble_timer.stop()
        self._bubble_timer.start(spec.duration_ms)

    def _stylesheet(self) -> str: 
        return """
        #card {
            background-color: rgba(10, 18, 33, 245);
            border: 1px solid #22314F;
            border-radius: 18px;
        }
        QLabel { color: #E8ECF6; background: transparent; }
        QLabel#tip { color: #9EB0E0; font-size: 12px; }
        QLabel#section { color: #9EB0E0; font-size: 12px; font-weight: 600; }
        QLabel#error { color: #FFBDC4; font-size: 12px; }
        QLabel#contextDot { font-size: 14px; color: #6EA8FC; }
        QTextEdit#contextView {
            background-color: #0B1428;
            color: #B9C6E4;
            border: 1px solid #1B2740;
            border-radius: 8px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 11px;
        }
        QTextEdit#promptEdit {
            background-color: #0B1428;
            color: #DCE6FF;
            border: 1px solid #24365A;
            border-radius: 8px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 11px;
        }
        QTextEdit {
            background-color: #080D1C;
            color: #ECECEC;
            border: 1px solid #1B2740;
            border-radius: 8px;
            font-family: Consolas, 'Courier New', monospace;
            font-size: 12px;
        }
        QPushButton {
            background-color: #16223A;
            color: #DCE6FF;
            border: 1px solid #2A3A5C;
            border-radius: 8px;
            padding: 6px 10px;
            font-size: 12px;
        }
        QPushButton:hover { background-color: #22335A; }
        QPushButton:pressed { background-color: #2C3F6B; }
        QPushButton#copy {
            padding: 0px;
            min-width: 24px;
            max-width: 24px;
            min-height: 22px;
            max-height: 22px;
            font-size: 15px;
            background-color: #12203A;
            border: 1px solid #24365A;
            color: #A9BEE8;
        }
        QPushButton#copy:hover {
            background-color: #21386A;
            border: 1px solid #3E5C9E;
            color: #FFFFFF;
        }
        QPushButton#copy:pressed {
            background-color: #2C4A86;
            border: 1px solid #4A6CC0;
        }
        QPushButton#settingsToggle {
            text-align: left;
            background-color: #101B30;
            color: #9EB0E0;
        }
        QPushButton#signinBanner {
            background-color: #F2A33C;
            color: #1B1206;
            border: none;
            border-radius: 10px;
            padding: 10px 16px;
            font-size: 14px;
            font-weight: 700;
        }
        QPushButton#signinBanner:hover { background-color: #FFBB55; }
        QPushButton#signinBanner:pressed { background-color: #D98A26; }
        QPushButton#signinBanner:disabled { background-color: #7A6338; color: #3A2F18; }
        QFrame#categoryCard {
            background-color: #0C1526;
            border: 1px solid #24365A;
            border-radius: 10px;
        }
        QPushButton#addCategoryButton {
            background-color: #12203A;
            color: #9EE0B8;
            border: 1px dashed #2A5C42;
        }
        QPushButton#removeCategoryButton {
            background-color: #2A1620;
            color: #E09EB0;
            border: 1px solid #5C2A3A;
            padding: 3px 8px;
            font-size: 11px;
        }
        QPushButton#iconbtn {
            padding: 0px;
            min-width: 44px;
            max-width: 44px;
            min-height: 40px;
            max-height: 40px;
            border-radius: 12px;
            font-size: 20px;
            background-color: #16223A;
        }
        QPushButton#iconbtn:hover { background-color: #22335A; }
        QPushButton#iconbtn:pressed { background-color: #2C3F6B; }
        #settingsPanel { background: transparent; }
        #historyPanel { background: transparent; }
        #historyRow {
            background: #0C1327; border: 1px solid #1E2A47; border-radius: 8px;
        }
        #historyRow QLabel { color: #C7D2E8; font-size: 11px; }
        QPushButton#historyCopy {
            background: #16203B; color: #9EB0E0; border: 1px solid #26365C;
            border-radius: 6px; padding: 2px 6px; font-size: 10px;
        }
        QPushButton#historyCopy:hover { background: #1E2A47; }
        #body, #bodyScroll, #bodyScroll > QWidget > QWidget { background: transparent; }
        QScrollArea#bodyScroll { border: none; background: transparent; }
        QLabel#appName { color: #C7D2E8; font-size: 12px; font-weight: 600; }
        QScrollBar:vertical {
            background: transparent; width: 8px; margin: 2px 0;
        }
        QScrollBar::handle:vertical {
            background: #2A3A5C; border-radius: 4px; min-height: 24px;
        }
        QScrollBar::handle:vertical:hover { background: #3A4E78; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        #settingsPanel QLabel { color: #9EB0E0; font-size: 11px; }
        QLineEdit, QComboBox {
            background-color: #080D1C;
            color: #ECECEC;
            border: 1px solid #2A3A5C;
            border-radius: 6px;
            padding: 3px 6px;
            font-size: 12px;
        }
        QComboBox QAbstractItemView {
            background-color: #0A1221;
            color: #ECECEC;
            selection-background-color: #22335A;
        }
        """

    def toggle_shrink(self) -> None:
        if self._collapsed:
            self._expand()
        else:
            self._collapse()

    def _collapse(self) -> None:
        self._collapsed = True
        self.unsetCursor()
        self.app_indicator.hide()
        self.body_scroll.hide()
        self.card.setStyleSheet("#card { background: transparent; border: none; }")
        self._orb_radius = 44
        self.orb.setFixedSize(88, 88)
        self.setMinimumSize(0, 0)
        self.body_scroll.setMaximumHeight(16777215)
        self.adjustSize()
        self.resize(self.minimumSizeHint())

    def _expand(self) -> None:
        self._collapsed = False
        self._transitioning = True
        self._hide_bubble()
        self._hide_badge()
        self.app_indicator.show()
        self.body_scroll.show()
        for widget in (
            self.status,
            self.tip,
            self.start_button,
            self.stop_button,
            self.shrink_button,
            self.settings_toggle,
            self.history_toggle,
            self.quit_button,
            self.details,
        ):
            widget.show()
        self.settings_panel.setVisible(self._settings_open)
        self.history_panel.setVisible(self._history_open)
        self.card.setStyleSheet("")
        self.setStyleSheet(self._stylesheet())
        self._orb_radius = 66
        self.orb.setFixedSize(132, 132)
        self._refresh_context_panel()
        self.setMinimumWidth(360)

        def _finish() -> None:
            self._transitioning = False
            # Restore the last edge-dragged size if the user set one.
            if self._user_size is not None:
                self._programmatic_resize(self._user_size.width(), self._user_size.height())
            self._fit_height()

        QTimer.singleShot(0, _finish)

    def _edge_at(self, pos) -> "Qt.Edge":
        """Which window edge(s) the cursor is over, for edge-drag resizing. Corners use
        a larger grab box so the diagonal-resize zones are easy to hit. Returns an
        empty flag when not near any edge (or when collapsed)."""
        edges = Qt.Edge(0)
        if self._collapsed:
            return edges
        m = self._resize_margin
        c = m + 10  # corners are easier to grab than thin edges
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        left = x <= m
        right = x >= w - m
        top = y <= m
        bottom = y >= h - m
        # Enlarge the four corner zones so diagonal resizing is reliable.
        if x <= c and y <= c:
            left, top = True, True
        elif x >= w - c and y <= c:
            right, top = True, True
        elif x <= c and y >= h - c:
            left, bottom = True, True
        elif x >= w - c and y >= h - c:
            right, bottom = True, True
        if left:
            edges |= Qt.Edge.LeftEdge
        elif right:
            edges |= Qt.Edge.RightEdge
        if top:
            edges |= Qt.Edge.TopEdge
        elif bottom:
            edges |= Qt.Edge.BottomEdge
        return edges

    # Windows non-client hit-test codes for edge/corner resize.
    _HT_CODES = {
        (True, False, True, False): 13,   # top-left     HTTOPLEFT
        (False, False, True, False): 12,  # top          HTTOP
        (False, True, True, False): 14,   # top-right    HTTOPRIGHT
        (True, False, False, False): 10,  # left         HTLEFT
        (False, True, False, False): 11,  # right        HTRIGHT
        (True, False, False, True): 16,   # bottom-left  HTBOTTOMLEFT
        (False, False, False, True): 15,  # bottom       HTBOTTOM
        (False, True, False, True): 17,   # bottom-right HTBOTTOMRIGHT
    }

    def _ht_code(self, edges):
        """Map edge flags to a Windows WM_NCHITTEST border code, or None."""
        key = (
            bool(edges & Qt.Edge.LeftEdge),
            bool(edges & Qt.Edge.RightEdge),
            bool(edges & Qt.Edge.TopEdge),
            bool(edges & Qt.Edge.BottomEdge),
        )
        return self._HT_CODES.get(key)

    def _programmatic_resize(self, width: int, height: int) -> None:
        """Resize without marking it as a user-driven size (so `resizeEvent` doesn't
        record it as the remembered manual size)."""
        self._programmatic = True
        self.resize(width, height)
        self._programmatic = False

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # A non-programmatic resize while expanded is the user dragging an edge —
        # remember it so _fit_height stops auto-sizing over their choice.
        if not self._collapsed and not self._programmatic and not self._transitioning:
            self._user_size = self.size()

    def nativeEvent(self, eventType, message):  # noqa: N802
        """Handle Windows WM_NCHITTEST so the OS treats the panel's outer border as a
        resizable window edge (native resize cursors + drag), even over child widgets.
        This is far more reliable than Qt-side edge tracking on a frameless window."""
        if (
            not self._collapsed
            and platform.system() == "Windows"
            and eventType == b"windows_generic_MSG"
        ):
            try:
                import ctypes
                from ctypes import wintypes

                class _MSG(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", wintypes.HWND),
                        ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM),
                        ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD),
                        ("pt_x", wintypes.LONG),
                        ("pt_y", wintypes.LONG),
                    ]

                msg = _MSG.from_address(int(message))
                if msg.message == 0x0084:  # WM_NCHITTEST
                    # Use Qt's logical cursor position (DPI-correct) rather than the
                    # physical-pixel lParam coords, which break on scaled displays.
                    local = self.mapFromGlobal(QCursor.pos())
                    code = self._ht_code(self._edge_at(local))
                    if code is not None:
                        return True, code
            except BaseException:
                pass
        return super().nativeEvent(eventType, message)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._moved = False
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            if (new_pos - self.pos()).manhattanLength() > 3:
                self._moved = True
            self.move(new_pos)
            if self._collapsed and self._badge.isVisible():
                self._position_badge()
            if self._collapsed and self._bubble.isVisible():
                self._position_bubble()
            if self._collapsed and self._context_bubble.isVisible():
                self._position_context_bubble()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        was_click = self._drag_offset is not None and not self._moved
        self._drag_offset = None
        if was_click and self._collapsed:
            self._expand()

    def start_hotkey(self) -> None:
        """Start (or restart) the global hotkey listener.

        The listener is a pynput low-level keyboard hook running on its own
        thread. Windows silently drops such hooks across sleep/resume, session
        lock, or if the hook thread ever raises -- after which the hotkey is
        dead until the listener is re-created. We therefore (a) always tear down
        any prior listener before creating a new one and (b) rely on
        ``_ensure_hotkey_alive`` (a watchdog timer) to re-arm it when it dies or
        the machine wakes from sleep."""
        prev = self.hotkey_listener
        self.hotkey_listener = None
        if prev is not None:
            try:
                prev.stop()
            except BaseException:  # noqa: BLE001
                pass
        try:
            self.hotkey_listener = keyboard.GlobalHotKeys(
                {normalize_hotkey(self.hotkey): self.hotkey_pressed.emit}
            )
            self.hotkey_listener.start()
        except BaseException as exc:  # noqa: BLE001
            self.hotkey_listener = None
            print(f"[hotkey] failed to start listener: {exc!r}", flush=True)

    def _install_hotkey_watchdog(self) -> None:
        """Self-heal the global hotkey. Restart the listener if its thread died,
        or if a large wall-clock gap between ticks reveals the machine slept
        (Windows drops the keyboard hook on resume while the thread stays alive,
        so liveness alone can't detect it)."""
        self._hotkey_watch_last = time.monotonic()
        self._hotkey_timer = QTimer(self)
        self._hotkey_timer.setInterval(4000)
        self._hotkey_timer.timeout.connect(self._ensure_hotkey_alive)
        self._hotkey_timer.start()

    def _ensure_hotkey_alive(self) -> None:
        now = time.monotonic()
        gap = now - self._hotkey_watch_last
        self._hotkey_watch_last = now
        listener = self.hotkey_listener
        alive = bool(listener is not None and listener.is_alive())
        # gap >> interval => process was suspended (sleep/hibernate); the hook is
        # likely gone even if the thread is still alive, so force a re-arm.
        slept = gap > 12.0
        if not alive or slept:
            reason = "thread-dead" if not alive else "resume-from-sleep"
            print(f"[hotkey] re-arming listener ({reason})", flush=True)
            self.start_hotkey()

    def _build_settings_panel(self) -> QWidget:
        """Build the inline settings form as collapsible categories (collapsed by default)."""
        self._settings_editors: dict[str, QWidget] = {}
        self._settings_rows: dict[str, QWidget] = {}
        self._settings_sections: list[tuple[QPushButton, QWidget, str]] = []
        cfg = _config.load_config(reload=True)

        panel = QWidget()
        panel.setObjectName("settingsPanel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(4, 6, 4, 6)
        outer.setSpacing(4)

        for sid, fields in _SETTINGS_CATEGORIES:
            section_title = t(f"settings.section.{sid}")
            header = QPushButton(f"{section_title}  ▸")
            header.setObjectName("settingsToggle")
            header.setCheckable(False)
            body = QWidget()
            body_form = QFormLayout(body)
            body_form.setContentsMargins(8, 4, 4, 4)
            body_form.setSpacing(6)
            body_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            body.setVisible(False)

            for key, kind, options in fields:
                label = _field_label(key)
                value = _config_get(cfg, key)
                if kind == "action":
                    btn = QPushButton(label)
                    btn.setObjectName("settingsToggle")
                    if key == "_download_model":
                        btn.clicked.connect(self._download_selected_model)
                    self._settings_rows[key] = btn
                    body_form.addRow(btn)
                    continue
                if kind == "combo":
                    editor: QWidget = QComboBox()
                    editor.addItems(list(options))
                    if value and value not in options:
                        editor.addItem(value)
                    editor.setCurrentText(value or (options[0] if options else ""))
                    # The model list is a convenience, not exhaustive — let the user
                    # type any faster-whisper repo id / size as well.
                    if key == "model":
                        editor.setEditable(True)
                elif kind == "toggle":
                    editor = QCheckBox()
                    editor.setChecked(_config_get_bool(cfg, key))
                else:
                    editor = QLineEdit(value)
                self._settings_editors[key] = editor
                row = QWidget()
                row_form = QFormLayout(row)
                row_form.setContentsMargins(0, 0, 0, 0)
                row_form.setSpacing(0)
                row_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
                row_form.addRow(label, editor)
                self._settings_rows[key] = row
                body_form.addRow(row)

            header.clicked.connect(lambda _=False, b=body, h=header, s=sid: self._toggle_section(b, h, s))
            self._settings_sections.append((header, body, sid))
            outer.addWidget(header)
            outer.addWidget(body)

            if sid == "polish":
                cat_header, cat_body = self._build_categories_section(cfg)
                outer.addWidget(cat_header)
                outer.addWidget(cat_body)

        # Re-evaluate field visibility when backend / polish engine change.
        for key in ("backend", "polish_engine"):
            editor = self._settings_editors.get(key)
            if isinstance(editor, QComboBox):
                editor.currentTextChanged.connect(lambda _=None: self._update_field_visibility())

        # Interface language applies live, without needing to press Save.
        ui_lang_combo = self._settings_editors.get("ui_language")
        if isinstance(ui_lang_combo, QComboBox):
            ui_lang_combo.currentTextChanged.connect(self._on_ui_language_changed)

        self.save_settings_button = QPushButton(t("btn.save"))
        self.save_settings_button.clicked.connect(self._save_settings)
        outer.addWidget(self.save_settings_button)

        self._sync_polish_combo()
        self._update_field_visibility()
        return panel

    # ---- Polish categories (user-editable, full CRUD) -------------------------

    def _build_categories_section(self, cfg: dict) -> tuple[QPushButton, QWidget]:
        """Build the collapsible section that lets the user add / remove / edit the
        polish categories (label, color, app keywords, prompt)."""
        header = QPushButton(f"{t('settings.section.categories')}  ▸")
        header.setObjectName("settingsToggle")
        header.setCheckable(False)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 4, 4, 4)
        body_layout.setSpacing(6)
        body.setVisible(False)

        note = QLabel(t("categories.note"))
        note.setObjectName("promptNote")
        note.setWordWrap(True)
        body_layout.addWidget(note)

        # Container that holds one editable card per category.
        self._categories_container = QWidget()
        self._categories_layout = QVBoxLayout(self._categories_container)
        self._categories_layout.setContentsMargins(0, 0, 0, 0)
        self._categories_layout.setSpacing(8)
        body_layout.addWidget(self._categories_container)

        add_btn = QPushButton(t("categories.add"))
        add_btn.setObjectName("addCategoryButton")
        add_btn.clicked.connect(self._add_blank_category)
        body_layout.addWidget(add_btn)

        header.clicked.connect(
            lambda _=False, b=body, h=header: self._toggle_section(b, h, "categories")
        )
        self._category_editors: list[dict] = []
        self._rebuild_category_cards(cfg)
        return header, body

    def _rebuild_category_cards(self, cfg: dict) -> None:
        """Clear and repopulate the category cards from the given config."""
        if not hasattr(self, "_categories_layout"):
            return
        while self._categories_layout.count():
            item = self._categories_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._category_editors = []
        cats = cfg.get("polish_categories")
        if not isinstance(cats, list) or not cats:
            from . import polish as _polish

            cats = [dict(c) for c in _polish.BUILTIN_CATEGORIES]
        for cat in cats:
            if isinstance(cat, dict) and cat.get("key"):
                self._add_category_card(cat)

    def _add_blank_category(self) -> None:
        existing = {e["key"].text().strip() for e in getattr(self, "_category_editors", [])}
        i = 1
        while f"custom{i}" in existing:
            i += 1
        self._add_category_card({
            "key": f"custom{i}",
            "label": f"自定义 Custom {i}",
            "color": "#8892A6",
            "keywords": [],
            "prompt": "",
        })
        QTimer.singleShot(0, self._fit_height)

    def _add_category_card(self, cat: dict) -> None:
        card = QFrame()
        card.setObjectName("categoryCard")
        form = QFormLayout(card)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        key_edit = QLineEdit(str(cat.get("key", "")))
        label_edit = QLineEdit(str(cat.get("label", "")))
        color_edit = QLineEdit(str(cat.get("color", "")))
        keywords = cat.get("keywords") or []
        if isinstance(keywords, (list, tuple)):
            keywords_text = ", ".join(str(k) for k in keywords)
        else:
            keywords_text = str(keywords)
        keywords_edit = QLineEdit(keywords_text)
        prompt_edit = ResizableTextEdit()
        prompt_edit.setObjectName("promptEdit")
        prompt_edit.setPlainText(str(cat.get("prompt", "")))
        prompt_edit.setFixedHeight(96)
        prompt_edit.setAcceptRichText(False)

        form.addRow("Key", key_edit)
        form.addRow(t("categories.field.label"), label_edit)
        form.addRow(t("categories.field.color"), color_edit)
        form.addRow(t("categories.field.keywords"), keywords_edit)
        form.addRow(t("categories.field.prompt"), prompt_edit)

        remove_btn = QPushButton(t("categories.remove"))
        remove_btn.setObjectName("removeCategoryButton")
        entry = {
            "widget": card,
            "key": key_edit,
            "label": label_edit,
            "color": color_edit,
            "keywords": keywords_edit,
            "prompt": prompt_edit,
        }
        remove_btn.clicked.connect(lambda _=False, e=entry: self._remove_category_card(e))
        form.addRow(remove_btn)

        self._categories_layout.addWidget(card)
        self._category_editors.append(entry)

    def _remove_category_card(self, entry: dict) -> None:
        if entry in self._category_editors:
            self._category_editors.remove(entry)
        w = entry.get("widget")
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        QTimer.singleShot(0, self._fit_height)

    def _collect_categories(self) -> list[dict]:
        """Read the category cards into a list of dicts, skipping blank keys."""
        cats: list[dict] = []
        seen: set[str] = set()
        for entry in getattr(self, "_category_editors", []):
            key = entry["key"].text().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            raw_kw = entry["keywords"].text().strip()
            keywords = [k.strip() for k in re.split(r"[,，\s]+", raw_kw) if k.strip()]
            cats.append({
                "key": key,
                "label": entry["label"].text().strip() or key,
                "color": entry["color"].text().strip() or "#8892A6",
                "keywords": keywords,
                "prompt": entry["prompt"].toPlainText().strip(),
            })
        return cats

    def _sync_polish_combo(self) -> None:
        """Make the 润色 combo offer off / auto plus every current category key."""
        combo = self._settings_editors.get("polish")
        if not isinstance(combo, QComboBox):
            return
        current = combo.currentText().strip()
        keys: list[str] = []
        for entry in getattr(self, "_category_editors", []):
            k = entry["key"].text().strip()
            if k and k not in keys:
                keys.append(k)
        if not keys:
            from . import polish as _polish

            keys = [c["key"] for c in _polish.BUILTIN_CATEGORIES]
        options = ["off", "auto"] + keys
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(options)
        if current and current not in options:
            combo.addItem(current)
        combo.setCurrentText(current or "off")
        combo.blockSignals(False)

    def _toggle_section(self, body: QWidget, header: QPushButton, section_id: str) -> None:
        show = not body.isVisible()
        body.setVisible(show)
        title = t(f"settings.section.{section_id}")
        header.setText(f"{title}  {'▾' if show else '▸'}")
        QTimer.singleShot(0, self._fit_height)

    def _update_field_visibility(self) -> None:
        """Show/hide rows (and empty category sections) based on backend/polish engine."""
        backend = ""
        polish_engine = ""
        be = self._settings_editors.get("backend")
        pe = self._settings_editors.get("polish_engine")
        if isinstance(be, QComboBox):
            backend = be.currentText().strip()
        if isinstance(pe, QComboBox):
            polish_engine = pe.currentText().strip()

        for key, row in self._settings_rows.items():
            row.setVisible(_field_applies(key, backend, polish_engine))

        # Hide a whole section if none of its fields apply.
        for header, body, section_id in self._settings_sections:
            keys = [k for k, _kd, _o in dict(_SETTINGS_CATEGORIES)[section_id]]
            any_visible = any(_field_applies(k, backend, polish_engine) for k in keys)
            header.setVisible(any_visible)
            if not any_visible:
                body.setVisible(False)
        QTimer.singleShot(0, self._fit_height)

    def toggle_settings(self) -> None:
        self._settings_open = not self._settings_open
        if self._settings_open:
            self._refresh_settings_editors()
        self.settings_panel.setVisible(self._settings_open)
        arrow = "▾" if self._settings_open else "▸"
        self.settings_toggle.setText(f"{t('toggle.settings')}  {arrow}")
        # The visibility change is applied on the next event-loop turn; resize after
        # that so the top-level window shrinks back down when the panel is hidden.
        QTimer.singleShot(0, self._fit_height)

    def toggle_history(self) -> None:
        self._history_open = not self._history_open
        self.history_panel.setVisible(self._history_open)
        self._update_history_toggle_text()
        QTimer.singleShot(0, self._fit_height)

    def _update_history_toggle_text(self) -> None:
        arrow = "▾" if self._history_open else "▸"
        count = len(self._history)
        suffix = f" ({count})" if count else ""
        self.history_toggle.setText(f"{t('toggle.history')}{suffix}  {arrow}")

    def _add_history_entry(self, raw_text: str, polished: str, target: "FocusTarget | None") -> None:
        """Record a finished dictation so concurrent jobs never overwrite each other."""
        text = (polished or raw_text or "").strip()
        if not text:
            return
        app_name = (getattr(target, "name", "") if target else "") or ""
        entry = {
            "raw": raw_text,
            "polished": polished or raw_text,
            "app": app_name,
            "time": time.strftime("%H:%M:%S"),
        }
        self._history.insert(0, entry)
        del self._history[30:]  # keep the list bounded
        self._rebuild_history()

    def _rebuild_history(self) -> None:
        layout = self._history_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        if not self._history:
            layout.addWidget(self._history_empty)
            self._history_empty.show()
            self._update_history_toggle_text()
            return
        for entry in self._history:
            layout.addWidget(self._build_history_row(entry))
        self._update_history_toggle_text()
        QTimer.singleShot(0, self._fit_height)

    def _build_history_row(self, entry: dict) -> QWidget:
        row = QWidget()
        row.setObjectName("historyRow")
        hl = QHBoxLayout(row)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(6)
        text = (entry.get("polished") or entry.get("raw") or "").strip()
        preview = text.replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:80] + "…"
        meta = entry.get("time", "")
        if entry.get("app"):
            meta = f"{meta} · {entry['app']}"
        label = QLabel(f"<span style='color:#8aa0c0'>{meta}</span><br>{preview}")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        copy_btn = QPushButton(t("btn.copy"))
        copy_btn.setObjectName("historyCopy")
        copy_btn.setFixedWidth(52)
        copy_btn.clicked.connect(lambda _=False, txt=text: self._copy_history_text(txt))
        hl.addWidget(label, 1)
        hl.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _copy_history_text(self, text: str) -> None:
        try:
            pyperclip.copy(text)
            self.error.setText(t("msg.copied_history"))
        except pyperclip.PyperclipException as exc:
            self.error.setText(t("status.copy_failed", error=exc))

    def _fit_height(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.activate()
        if self._collapsed:
            self._programmatic_resize(self.width(), self.sizeHint().height())
            return
        # Cap the scrollable body so the whole window never exceeds the screen; the
        # body scrolls when its content (settings, prompts, transcript) is taller.
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry().height() if screen is not None else 900
        reserve = self.orb.height() + self.app_indicator.sizeHint().height() + 80
        body_cap = max(220, int(avail * 0.9) - reserve)
        content_h = self.body.sizeHint().height()
        if self._user_size is not None:
            # The user chose a size by dragging an edge — honor it and let the scroll
            # area fill whatever height they picked (scrollbar handles overflow).
            self.body_scroll.setMaximumHeight(16777215)
            return
        self.body_scroll.setMaximumHeight(min(content_h, body_cap))
        self._programmatic_resize(self.width(), self.sizeHint().height())

    def _refresh_settings_editors(self) -> None:
        """Reload editor values from the current config so unsaved edits are
        discarded when the panel is reopened (acts as a natural revert)."""
        cfg = _config.load_config(reload=True)
        for key, editor in self._settings_editors.items():
            value = _config_get(cfg, key)
            if isinstance(editor, QComboBox):
                if value and editor.findText(value) < 0:
                    editor.addItem(value)
                editor.setCurrentText(value)
            elif isinstance(editor, QCheckBox):
                editor.setChecked(_config_get_bool(cfg, key))
            elif isinstance(editor, QTextEdit):
                editor.setPlainText(value)
            elif isinstance(editor, QLineEdit):
                editor.setText(value)
        self._rebuild_category_cards(cfg)
        self._sync_polish_combo()
        self._update_field_visibility()

    def _collect_settings(self) -> dict:
        updates: dict = {}
        for key, editor in self._settings_editors.items():
            if isinstance(editor, QCheckBox):
                updates[key] = editor.isChecked()
                continue
            if isinstance(editor, QComboBox):
                value: str = editor.currentText().strip()
            elif isinstance(editor, QTextEdit):
                value = editor.toPlainText().strip()
            else:
                value = editor.text().strip()
            if "." in key:
                section, sub = key.split(".", 1)
                updates.setdefault(section, {})[sub] = value
            elif key == "max_record_seconds":
                try:
                    updates[key] = max(0, int(value))
                except ValueError:
                    updates[key] = 120
            else:
                updates[key] = value
        updates["polish_categories"] = self._collect_categories()
        return updates

    def _save_settings(self) -> None:
        updates = self._collect_settings()
        try:
            path = _config.save_config(updates)
        except OSError as exc:
            self.error.setText(t("msg.settings_save_failed", error=exc))
            return
        self.apply_settings(_config.load_config(reload=True))
        self.error.setText(t("msg.settings_saved", name=path.name))

    def _copy_field(self, edit: QTextEdit, label: str) -> None:
        text = edit.toPlainText().strip()
        if not text:
            self.error.setText(t("msg.field_empty", label=label))
            return
        pyperclip.copy(text)
        self.error.setText(t("msg.copied_field", label=label))

    def _apply_polish_visibility(self) -> None:
        """Show the Active Context + Polished sections only when polishing is on.
        With polish off there is no polished text and no per-app polish mode, so
        both sections are hidden to keep the expanded panel compact."""
        enabled = str(self.polish).strip().lower() != "off"
        if hasattr(self, "context_section"):
            self.context_section.setVisible(enabled)
        if hasattr(self, "polished_section"):
            self.polished_section.setVisible(enabled)
        QTimer.singleShot(0, self._fit_height)

    def _greet_second_instance(self) -> None:
        """A second launch was attempted. Surface THIS instance instead of letting
        a duplicate open: raise it, bounce the orb, and pop a friendly bubble so
        the user sees the app is already running (avoids duplicate orbs and global
        hotkey conflicts)."""
        try:
            if not self.isVisible():
                self.showNormal()
            self.raise_()
            self.activateWindow()
            self.enforce_topmost()
            self._bounce_orb()
            self._show_bubble(t("bubble.already_running"), final=True)
        except Exception:  # noqa: BLE001
            pass

    def apply_settings(self, cfg: dict) -> None:
        """Apply saved config to the live overlay so changes take effect without a restart."""
        # UI language: switch and retranslate live if it changed.
        new_lang = resolve_language(cfg.get("ui_language"))
        lang_changed = new_lang != current_language()
        if lang_changed:
            set_language(cfg.get("ui_language"))
        self.language = cfg.get("language", self.language)
        self.model_name = cfg.get("model", self.model_name)
        self.backend = cfg.get("backend", self.backend)
        self.mlx_model = cfg.get("mlx_model", self.mlx_model)
        self.hf_endpoint = cfg.get("hf_endpoint", self.hf_endpoint)
        self.polish = cfg.get("polish", self.polish)
        self.polish_engine = cfg.get("polish_engine", self.polish_engine)
        self.ollama_model = cfg.get("ollama_model", self.ollama_model)
        self.language_preference = cfg.get("language_preference", self.language_preference)
        self.copy_to_clipboard = _config_get_bool(cfg, "copy_to_clipboard")
        self.paste_to_active_app = _config_get_bool(cfg, "paste_to_active_app")
        self.submit_to_active_app = _config_get_bool(cfg, "submit_to_active_app")
        # Keep the OS "launch on login" entry in sync with the setting.
        get_platform_services().set_launch_at_startup(
            _config_get_bool(cfg, "launch_at_startup")
        )
        # Toggling polish on/off reveals or hides the context/polished sections.
        self._apply_polish_visibility()

        new_hotkey = cfg.get("hotkey", self.hotkey)
        if new_hotkey != self.hotkey:
            if self.hotkey_listener is not None:
                self.hotkey_listener.stop()
            self.hotkey = new_hotkey
            self.start_hotkey()
        self.tip.setText(t("label.hotkey", hotkey=self.hotkey))

        if lang_changed:
            self._retranslate_ui()

        if self.backend == "azure" or self.polish_engine == "azure":
            from . import azure_client

            threading.Thread(target=azure_client.warmup, daemon=True).start()

    def _retranslate_ui(self) -> None:
        """Refresh all static UI strings after the interface language changes, and
        rebuild the settings panel so its labels pick up the new language."""
        self.setWindowTitle(t("window.title"))
        self.tip.setText(t("label.hotkey", hotkey=self.hotkey))
        self.start_button.setToolTip(t("btn.start.tip"))
        self.stop_button.setToolTip(t("btn.stop.tip"))
        self.shrink_button.setToolTip(t("btn.shrink.tip"))
        self.quit_button.setToolTip(t("btn.quit.tip"))
        self.relaunch_button.setToolTip(t("btn.relaunch.tip"))
        self.copy_raw_button.setToolTip(t("btn.copy_raw.tip"))
        self.copy_polished_button.setToolTip(t("btn.copy_polished.tip"))
        self._raw_title.setText(t("label.raw_transcript"))
        self._context_title.setText(t("label.active_context"))
        self._polished_title.setText(t("label.polished"))
        self.transcript.setPlaceholderText(t("ph.transcript"))
        self.context_view.setPlaceholderText(t("ph.context"))
        self.polished.setPlaceholderText(t("ph.polished"))
        self.signin_btn.setToolTip(t("btn.signin.tip"))
        self.signin_btn.setText(t("btn.signin"))
        self._history_empty.setText(t("label.history_empty"))
        arrow = "▾" if self._settings_open else "▸"
        self.settings_toggle.setText(f"{t('toggle.settings')}  {arrow}")
        self._update_history_toggle_text()

        # Rebuild the settings panel in place so its section titles, field labels,
        # buttons and category cards are re-rendered in the new language. Preserve
        # which sections were expanded so a live language switch isn't jarring.
        expanded = {
            sid for _h, body, sid in getattr(self, "_settings_sections", [])
            if body.isVisible()
        }
        idx = self._body_layout.indexOf(self.settings_panel)
        old = self.settings_panel
        self.settings_panel = self._build_settings_panel()
        self.settings_panel.setVisible(self._settings_open)
        for header, body, sid in self._settings_sections:
            if sid in expanded:
                body.setVisible(True)
                header.setText(f"{t(f'settings.section.{sid}')}  ▾")
        if idx >= 0:
            self._body_layout.insertWidget(idx, self.settings_panel)
        else:
            self._body_layout.addWidget(self.settings_panel)
        old.setParent(None)
        old.deleteLater()
        QTimer.singleShot(0, self._fit_height)

    def _on_ui_language_changed(self, value: str) -> None:
        """Switch the interface language immediately when the user picks a new value
        in the settings combo (no need to press Save), persisting the choice."""
        if resolve_language(value) == current_language():
            return
        try:
            _config.save_config({"ui_language": value})
        except OSError:
            pass
        set_language(value)
        # Defer the rebuild so we don't delete the combo inside its own signal.
        QTimer.singleShot(0, self._retranslate_ui)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._hotkey_timer is not None:
            self._hotkey_timer.stop()
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        if self._topmost_timer is not None:
            self._topmost_timer.stop()
        if self._focus_timer is not None:
            self._focus_timer.stop()
        if self._token_timer is not None:
            self._token_timer.stop()
        event.accept()

    def _relaunch(self) -> None:
        """Spawn a fresh copy of this process with the original arguments, then quit."""
        subprocess.Popen([sys.executable] + sys.argv)
        QApplication.instance().quit()

    def toggle_recording(self) -> None:
        print("[hotkey] triggered", flush=True)
        streaming = getattr(self, "stream_worker", None) is not None and self.stream_worker.isRunning()
        if self.recorder.is_recording() or streaming:
            self.stop_recording()
        else:
            self.start_recording()

    def _use_realtime_stream(self) -> bool:
        if self.backend != "azure":
            return False
        try:
            from . import config as _cfg
            azure = _cfg.get_azure_config()
        except Exception:  # noqa: BLE001
            return False
        return str(azure.get("transcribe_mode", "batch")).strip().lower() == "realtime"

    def _max_record_seconds(self) -> int:
        try:
            from . import config as _cfg
            return int(_cfg.load_config().get("max_record_seconds", 120) or 0)
        except Exception:  # noqa: BLE001
            return 120

    def _start_max_record_timer(self) -> None:
        seconds = self._max_record_seconds()
        if seconds > 0:
            self._max_record_timer.start(seconds * 1000)

    def _on_max_record_timeout(self) -> None:
        recording = self.recorder.is_recording()
        streaming = getattr(self, "stream_worker", None) is not None and self.stream_worker.isRunning()
        if recording or streaming:
            self.stop_recording()

    def _target_polish_desc(self) -> str:
        """A short 'AppName → mode' description of the detected app and the polish
        style that will be applied, for confirming context in the status/tooltip."""
        if self.polish == "off":
            return ""
        target = self._recording_target or self._preferred_target
        name = (target.name if target else "") or ""
        if not name:
            return ""
        from .polish import resolve_polish_mode

        bundle = (target.bundle_id if target else "") or ""
        mode = resolve_polish_mode(
            self.polish, name, bundle,
            sub_kind=(target.sub_kind if target else "") or "",
            copilot_session=target.copilot_cli if target else False,
        )
        return f"{name} → {mode}"

    def _resolved_polish_mode(self) -> str:
        """The concrete polish category (dev/im/notes/email/browser/copilot) for the
        current recording target, or 'off' when polishing is disabled."""
        if self.polish == "off":
            return "off"
        from .polish import resolve_polish_mode

        target = self._recording_target or self._preferred_target
        name = (target.name if target else "") or ""
        bundle = (target.bundle_id if target else "") or ""
        return resolve_polish_mode(
            self.polish, name, bundle,
            sub_kind=(target.sub_kind if target else "") or "",
            copilot_session=target.copilot_cli if target else False,
        )

    def _app_icon_pixmap(self, size: int = 40, target: FocusTarget | None = None) -> QPixmap | None:
        """Best-effort icon for a target's executable (Windows/macOS)."""
        if target is None:
            target = self._recording_target or self._preferred_target
        exe = (getattr(target, "exe_path", "") if target else "") or ""
        if not exe:
            return None
        try:
            provider = QFileIconProvider()
            icon = provider.icon(QFileInfo(exe))
            if icon is None or icon.isNull():
                return None
            pm = icon.pixmap(QSize(size, size))
            return pm if not pm.isNull() else None
        except BaseException:
            return None

    def _set_app_indicator(self, target: FocusTarget | None, color: str, mode: str) -> None:
        """Update the inline 'active app' indicator (icon + name · label) shown next
        to the pet in the expanded card."""
        from .polish import polish_mode_label

        name = (target.name if target else "") or ""
        pretty = os.path.splitext(name)[0] if name else ""
        pm = self._app_icon_pixmap(18, target)
        if pm is not None and not pm.isNull():
            self.app_icon_label.setPixmap(pm)
        else:
            self.app_icon_label.clear()
        if pretty:
            self.app_name_label.setText(f"{pretty} · {polish_mode_label(mode)}")
        else:
            self.app_name_label.setText(t("label.app_unknown"))
        self.app_name_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    _SUB_KINDS = ("terminal", "editor", "chat", "browser", "document")

    @staticmethod
    def _sub_kind_label(sub_kind: str) -> str:
        """Localized label for a focus sub-kind, or '' if unknown."""
        sk = (sub_kind or "").strip()
        return t(f"subkind.{sk}") if sk in VoiceDesktop._SUB_KINDS else ""

    def _deep_enrich(self, target: FocusTarget | None) -> FocusTarget | None:
        """Best-effort deep inspection of the target (window title, focused control,
        terminal/editor/chat text) captured at record time. Degrades to the original
        target on any failure — never blocks recording."""
        if target is None:
            return None
        try:
            info = focus_context.enrich(
                target.system, target.hwnd, target.exe_path, target.name
            )
        except BaseException:
            return target
        if info.is_empty:
            return target
        return replace(
            target,
            title=info.title or target.title,
            sub_kind=info.sub_kind or target.sub_kind,
            content=info.content or target.content,
            session=info.session or target.session,
            copilot_cli=info.copilot_cli,
            plugins=tuple(info.plugins),
        )

    def _live_context_text(self, target: FocusTarget | None) -> str:
        """Compact 'what the user is focused on' string injected into the polish
        prompt so the model can adapt to the actual on-screen context."""
        if target is None:
            return ""
        parts: list[str] = []
        title = (target.title or "").strip()
        if title:
            parts.append(f"当前窗口：{title}")
        sub = self._sub_kind_label(target.sub_kind or "")
        if sub:
            parts.append(f"焦点区域：{sub}")
        content = (target.content or "").strip()
        if content:
            parts.append(f"焦点内容：{content}")
        session = _session_line(getattr(target, "session", None))
        if session:
            parts.append(session)
        for result in getattr(target, "plugins", ()) or ():
            text = (getattr(result, "text", "") or "").strip()
            if text:
                label = (getattr(result, "label", "") or "上下文").strip()
                parts.append(f"{label}：{text}")
        return "；".join(parts)

    def _focus_detail_lines(self, target: FocusTarget | None) -> str:
        """Human-readable focus detail for the expanded 'Active Context' panel."""
        if target is None:
            return ""
        lines: list[str] = []
        title = (target.title or "").strip()
        if title:
            lines.append(f"{t('ctx.window_title')}：{title}")
        sub = self._sub_kind_label(target.sub_kind or "")
        if sub:
            lines.append(f"{t('ctx.focus_area')}：{sub}")
        content = (target.content or "").strip()
        if content:
            snippet = content if len(content) <= 300 else content[:300] + "…"
            lines.append(f"{t('ctx.focus_content')}：{snippet}")
        session = _session_line(getattr(target, "session", None))
        if session:
            lines.append(session)
        for result in getattr(target, "plugins", ()) or ():
            text = (getattr(result, "text", "") or "").strip()
            if not text:
                continue
            label = (getattr(result, "label", "") or t("ctx.default_label")).strip()
            # Keep the newest end of the window (plugin text is oldest-first).
            snippet = text if len(text) <= 700 else "…" + text[-700:]
            lines.append(f"{label}：\n{snippet}")
        return "\n".join(lines)

    def _context_for(self, target: FocusTarget | None) -> tuple[str, str, str, str]:
        """Return (mode, color, app_name, panel_text) describing the polish context
        for ``target`` — used by both the collapsed badge and the expanded panel."""
        from .polish import (
            describe_polish_context,
            polish_mode_color,
            polish_mode_label,
            resolve_polish_mode,
        )

        name = (target.name if target else "") or ""
        bundle = (target.bundle_id if target else "") or ""
        mode = "off" if self.polish == "off" else resolve_polish_mode(
            self.polish, name, bundle,
            sub_kind=(target.sub_kind if target else "") or "",
            copilot_session=target.copilot_cli if target else False,
        )
        color = polish_mode_color(mode)
        label = polish_mode_label(mode)
        header = f"{name or t('ctx.unknown_app')} · {label}"
        detail = self._focus_detail_lines(target)
        body = describe_polish_context(mode, self.session_context or "")
        panel_text = header
        if detail:
            panel_text += f"\n\n{detail}"
        panel_text += f"\n\n{body}"
        return mode, color, name, panel_text

    def _refresh_context_panel(self) -> None:
        """Update the expanded 'Active Context' panel from the LIVE foreground app so
        the user can always see which app is currently active (even before recording)."""
        target = self._preferred_target or self._recording_target
        mode, color, _name, panel_text = self._context_for(target)
        self.context_view.setPlainText(panel_text)
        self.context_badge_dot.setStyleSheet(f"color: {color};")
        self._set_app_indicator(target, color, mode)

    def _update_context_view(self) -> None:
        """Refresh the badge (icon + category cord color) and the expanded
        'Active Context' text from the current recording target and polish mode."""
        target = self._recording_target or self._preferred_target
        mode, color, name, panel_text = self._context_for(target)

        # Expanded 'Active Context' text + inline indicator.
        self.context_view.setPlainText(panel_text)
        self.context_badge_dot.setStyleSheet(f"color: {color};")
        self._set_app_indicator(target, color, mode)

        # Collapsed badge visuals.
        pretty = os.path.splitext(name)[0] if name else ""
        self._badge.set_context(
            color=color,
            pixmap=self._app_icon_pixmap(28),
            letter=(pretty[:1] if pretty else "?"),
            label=pretty,
        )

    def _position_badge(self) -> None:
        """Anchor the badge just below the orb, its cord touching the orb bottom."""
        orb_tl = self.orb.mapToGlobal(self.orb.rect().topLeft())
        orb_center_x = orb_tl.x() + self.orb.width() // 2
        orb_bottom = orb_tl.y() + self.orb.height()
        top_local = self._badge.cord_top_local()
        x = orb_center_x - int(top_local.x())
        y = orb_bottom - int(top_local.y()) + 2  # cord starts just below the orb
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        if avail is not None:
            x = max(avail.left() + 4, min(x, avail.right() - self._badge.width() - 4))
            y = max(avail.top() + 4, min(y, avail.bottom() - self._badge.height() - 4))
        self._badge.move(int(x), int(y))

    def _show_badge(self) -> None:
        """Show the context badge below the orb when collapsed and a target/mode is
        known. Hidden if polishing is off or no app was detected."""
        target = self._recording_target or self._preferred_target
        name = (target.name if target else "") or ""
        if not self._collapsed or self.polish == "off" or not name:
            self._hide_badge()
            return
        was_hidden = not self._badge.isVisible()
        self._update_context_view()
        self._position_badge()
        self._badge_timer.stop()
        self._reveal(self._badge, animate=was_hidden)
        self._show_context_bubble()
        if was_hidden:
            self._pulse_cord()  # "pet connects to app" moment

    def _context_bubble_text(self) -> str:
        """Compact 'what the app side collected' string for the badge bubble."""
        target = self._recording_target or self._preferred_target
        if target is None:
            return ""
        lines: list[str] = []
        session = _session_line(getattr(target, "session", None))
        if session:
            lines.append(session)
        sub = self._sub_kind_label(target.sub_kind or "")
        title = (target.title or "").strip()
        if title:
            head = title if len(title) <= 100 else title[:100] + "…"
            lines.append(f"{sub}｜{head}" if sub else head)
        elif sub:
            lines.append(sub)
        content = (target.content or "").strip()
        if content:
            snippet = content if len(content) <= 220 else content[:220] + "…"
            lines.append(snippet)
        return "\n".join(lines)

    def _show_context_bubble(self) -> None:
        """Show the context bubble next to the badge (collapsed only)."""
        if not self._collapsed or not self._badge.isVisible():
            self._context_bubble.pop_out()
            return
        text = self._context_bubble_text()
        if not text:
            self._context_bubble.pop_out()
            return
        target = self._recording_target or self._preferred_target
        _mode, color, _name, _panel = self._context_for(target)
        spec = make_bubble(text, kind=BubbleKind.CONTEXT, accent=color, duration_ms=20_000)
        was_hidden = not self._context_bubble.isVisible()
        self._context_bubble.set_text(spec.text)
        self._context_bubble.set_accent(spec.accent)
        self._position_context_bubble()
        if was_hidden:
            self._context_bubble.pop_in()
        else:
            self._context_bubble.ensure_shown()

    def _position_context_bubble(self) -> None:
        """Anchor the context bubble beside the badge icon, tail pointing at it.
        Prefers the right of the badge, flips left if there isn't room."""
        m = SpeechBubble.SHADOW
        bs = ContextBadge.SHADOW
        badge_tl = self._badge.mapToGlobal(self._badge.rect().topLeft())
        icon_left = badge_tl.x() + bs
        icon_right = icon_left + ContextBadge.BADGE_D
        icon_center_y = (
            badge_tl.y() + bs + ContextBadge.CORD_LEN + ContextBadge.BADGE_D // 2
        )
        gap = -2

        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None

        tail_side = "left"  # bubble on the right of the badge
        self._context_bubble.set_tail_side(tail_side)
        bw = self._context_bubble.width()
        x = icon_right + gap - m
        if avail is not None and x + bw > avail.right() - 4:
            tail_side = "right"
            self._context_bubble.set_tail_side(tail_side)
            bw = self._context_bubble.width()
            x = icon_left - bw - gap + m
        bh = self._context_bubble.height()
        y = icon_center_y - (m + self._context_bubble._body_h // 2)
        if avail is not None:
            y = max(avail.top() + 4, min(y, avail.bottom() - bh - 4))
            x = max(avail.left() + 4, min(x, avail.right() - bw - 4))
        self._context_bubble.move(int(x), int(y))

    def _hide_badge(self) -> None:
        self._badge_timer.stop()
        self._fade_out(self._badge)
        self._context_bubble.pop_out()

    def _register_worker(self, worker: "QThread") -> None:
        """Track a background transcribe/polish thread so it isn't garbage-collected
        while running, and drop it automatically when it finishes."""
        self._active_workers.add(worker)
        worker.finished.connect(lambda w=worker: self._active_workers.discard(w))

    def _discard_worker(self, worker: "QThread | None") -> None:
        if worker is not None:
            self._active_workers.discard(worker)

    def start_recording(self) -> None:
        try:
            if self._collapsed:
                self._bounce_orb()
            self._hide_bubble()
            # Prefer the LIVE foreground app. On Windows the overlay is a
            # non-activating tool window, so the live target is the real app the
            # user is in; fall back to the last-remembered target only when the
            # live probe can't identify another app (e.g. macOS focus stealing).
            # Use only the CHEAP live probe here; the expensive deep UIA enrich is
            # deferred until after capture starts so F9 feels instant and no speech
            # at the start of the utterance is clipped.
            _fi = get_platform_services().get_frontmost_window(int(self.winId()))
            if _fi is not None:
                _live = FocusTarget(
                    system=platform.system(),
                    name=_fi.name,
                    bundle_id=_fi.bundle_id,
                    pid=_fi.pid,
                    hwnd=_fi.hwnd,
                    exe_path=_fi.exe_path,
                )
            else:
                _live = None
            self._recording_target = _live or self._preferred_target
            # Surface the context badge (icon + cord + gathered context) right away
            # so it's visible for the whole take — the deep UIA enrich below is slow
            # and would otherwise delay (or, for short takes, skip) the badge.
            self._show_badge()
            if self._use_realtime_stream():
                self._start_realtime_stream("")
                self._start_max_record_timer()
            else:
                self.recorder.start()
                self._start_max_record_timer()
                self._set_stage("recording")
                self.error.setText(t("status.recording"))
            # Enrich context (window title, focused control, session) after capture
            # has begun; this updates the polish context, badge and status suffix.
            QTimer.singleShot(0, self._enrich_recording_context)
        except BaseException as exc:  # noqa: BLE001
            self._set_stage("error")
            self.error.setText(t("status.start_failed", error=exc))

    def _enrich_recording_context(self) -> None:
        """Deferred heavy focus enrichment, run just after recording starts so the
        F9 press has immediate audio + visual feedback."""
        streaming = getattr(self, "stream_worker", None) is not None and self.stream_worker.isRunning()
        if not (self.recorder.is_recording() or streaming):
            return  # recording already stopped before enrichment ran
        self._recording_target = self._deep_enrich(self._recording_target)
        # Realtime stream captured the cheap target at creation; upgrade it so the
        # follow-up polish still gets the fully-enriched focus context.
        if streaming and self.stream_worker is not None:
            self.stream_worker.job_target = self._recording_target
        desc = self._target_polish_desc()
        if desc:
            self.orb.setToolTip(desc)
            base = t("status.streaming_realtime", status="") if streaming else t("status.recording")
            self.error.setText(f"{base} · {desc}")
        self._update_context_view()
        self._show_badge()

    def _start_realtime_stream(self, status_suffix: str = "") -> None:
        from . import config as _cfg
        from . import azure_client
        from .cli import build_azure_prompt, load_replacements

        _rt_log("_start_realtime_stream: enter")
        azure = _cfg.get_azure_config()
        lang_hint = azure_client.transcribe_language_hint(self.language_preference)
        replacement_map = load_replacements(self.replacements_file, self.replacement_pairs)
        prompt = build_azure_prompt(replacement_map)
        _rt_log("_start_realtime_stream: config ready, starting worker")

        self.transcript.clear()
        self.polished.clear()
        worker = RealtimeStreamWorker(azure, lang_hint, prompt)
        worker.job_target = self._recording_target
        self.stream_worker = worker
        worker.partial.connect(self._on_stream_partial)
        worker.finished_text.connect(lambda raw, w=worker: self._on_stream_finished(raw, w))
        worker.failed.connect(lambda msg, w=worker: self._on_failed(msg, w))
        worker.start()
        self._set_stage("recording")
        self.error.setText(t("status.streaming_realtime", status=status_suffix))

    def stop_recording(self) -> None:
        try:
            self._max_record_timer.stop()
            # The context bubble only helps while the user is still speaking; once
            # recording ends, retract it. The cord/badge stays as the live indicator
            # until the result lands.
            self._context_bubble.pop_out()
            if getattr(self, "stream_worker", None) is not None and self.stream_worker.isRunning():
                self._set_stage("transcribing")
                self.error.setText(t("status.finishing"))
                self.stream_worker.stop()
                return
            audio_path = self.recorder.stop()
            self._set_stage("transcribing")
            job_target = self._recording_target
            app_desc = f" [{job_target.name}]" if job_target and job_target.name else ""
            self.error.setText(t("status.transcribing", name=audio_path.name, app=app_desc))
            worker = TranscribeWorker(
                audio_path,
                self.model_name,
                self.backend,
                self.mlx_model,
                self.language,
                self.hf_endpoint,
                self.replacement_pairs,
                self.replacements_file,
                self.polish,
                self.context_file,
                self.session_context,
                self.language_preference,
                self.polish_engine,
                self.ollama_model,
                target_app_name=job_target.name if job_target else None,
                target_app_bundle_id=job_target.bundle_id if job_target else None,
                live_context=self._live_context_text(job_target),
                focus_sub_kind=job_target.sub_kind if job_target else "",
                copilot_session=job_target.copilot_cli if job_target else False,
            )
            worker.job_target = job_target
            self.worker = worker
            worker.finished_text.connect(
                lambda raw, pol, w=worker: self._on_transcribed(raw, pol, w)
            )
            worker.failed.connect(lambda msg, w=worker: self._on_failed(msg, w))
            self._register_worker(worker)
            worker.start()
        except BaseException as exc:  # noqa: BLE001
            self._set_stage("error")
            self.error.setText(t("status.stop_failed", error=exc))

    def _on_stream_partial(self, text: str) -> None:
        self._set_stage("streaming")
        self.transcript.setPlainText(text)
        self._show_bubble(text)

    def _on_stream_finished(self, raw_text: str, worker: "RealtimeStreamWorker | None" = None) -> None:
        if worker is not None and self.stream_worker is worker:
            self.stream_worker = None
        elif worker is None:
            self.stream_worker = None
        job_target = getattr(worker, "job_target", None) or self._recording_target
        self.transcript.setPlainText(raw_text)
        if not raw_text.strip():
            self._set_stage("error")
            self.error.setText(t("status.no_speech"))
            return
        self._show_bubble(raw_text)
        self._set_stage("transcribing")
        desc = self._target_polish_desc()
        self.error.setText(t("status.polishing", app=f" · {desc}" if desc else ""))
        pworker = PolishWorker(
            raw_text,
            self.polish,
            self.context_file,
            self.session_context,
            self.language_preference,
            self.polish_engine,
            self.ollama_model,
            target_app_name=job_target.name if job_target else None,
            target_app_bundle_id=job_target.bundle_id if job_target else None,
            live_context=self._live_context_text(job_target),
            focus_sub_kind=job_target.sub_kind if job_target else "",
            copilot_session=job_target.copilot_cli if job_target else False,
        )
        pworker.job_target = job_target
        self.polish_worker = pworker
        pworker.finished_text.connect(lambda raw, pol, w=pworker: self._on_transcribed(raw, pol, w))
        self._register_worker(pworker)
        pworker.start()

    def _on_transcribed(self, raw_text: str, polished: str, worker: "QThread | None" = None) -> None:
        job_target = getattr(worker, "job_target", None) if worker is not None else None
        self._discard_worker(worker)
        self.transcript.setPlainText(raw_text)
        self.polished.setPlainText(polished or raw_text)
        self._set_stage("done")
        self.error.setText(t("status.done"))
        self._show_bubble(polished or raw_text, final=True)
        # The green 'done' state is a brief success flourish, not a resting state:
        # settle back to idle shortly so the pet doesn't sit glowing green forever.
        QTimer.singleShot(1200, self._settle_done_to_idle)
        # Keep the context cord on screen until the text is backfilled, then linger
        # a few seconds so a long utterance never loses the indicator early.
        if self._collapsed and self._badge.isVisible():
            self._badge_timer.start(9000)
        text = polished or raw_text
        self._add_history_entry(raw_text, polished or raw_text, job_target)
        should_paste = self.paste_to_active_app or self.submit_to_active_app
        if should_paste:
            # _paste_text puts the text on the clipboard itself (paste reads it).
            self._paste_text(text, job_target)
        elif self.copy_to_clipboard:
            try:
                pyperclip.copy(text)
                self.error.setText(t("status.copied_clipboard"))
            except pyperclip.PyperclipException as exc:
                self.error.setText(t("status.copy_failed", error=exc))

    def _settle_done_to_idle(self) -> None:
        # Only revert if still showing the success state — a new recording started
        # in the meantime must not be clobbered back to idle.
        if self._stage == "done":
            self._set_stage("idle")

    def _on_failed(self, message: str, worker: "QThread | None" = None) -> None:
        if worker is not None and self.stream_worker is worker:
            self.stream_worker = None
        self._discard_worker(worker)
        self._set_stage("error")
        self.error.setText(message)
        # If the failure is really "not signed in", surface the sign-in button so
        # the user can recover in one click instead of decoding the error text.
        if self.backend == "azure" or self.polish_engine == "azure":
            self._check_auth_async(on_error_hint=message)
        if self._badge.isVisible():
            self._badge_timer.start(3000)

    # --- Azure sign-in --------------------------------------------------------
    def _check_auth_async(self, on_error_hint: str = "") -> None:
        """Query auth status off the UI thread (it may mint a cached token) and
        toggle the sign-in button accordingly."""
        worker = getattr(self, "_auth_worker", None)
        if worker is not None and worker.isRunning():
            return
        worker = AuthStatusWorker()
        worker.ready.connect(
            lambda status, hint=on_error_hint: self._apply_auth_status(status, hint)
        )
        worker.finished.connect(lambda w=worker: self._discard_worker(w))
        self._auth_worker = worker
        worker.start()

    def _apply_auth_status(self, status: dict, on_error_hint: str = "") -> None:
        signed_in = bool(status.get("signed_in", True))
        self.signin_btn.setVisible(not signed_in)
        if not signed_in:
            acct = status.get("account") or ""
            hint = t("signin.hint_suffix", acct=acct) if acct else ""
            self.signin_btn.setText(f"{t('btn.signin')}{hint}")
            if not on_error_hint:
                self.error.setText(t("msg.not_signed_in"))

    def _start_sign_in(self) -> None:
        if self._signin_worker is not None and self._signin_worker.isRunning():
            return
        self.signin_btn.setEnabled(False)
        self.signin_btn.setText(t("btn.signin_opening"))
        self.error.setText(t("msg.signin_browser"))
        worker = SignInWorker()
        worker.signed_in.connect(self._on_signed_in)
        worker.failed.connect(self._on_signin_failed)
        worker.finished.connect(lambda w=worker: self._discard_worker(w))
        self._signin_worker = worker
        worker.start()

    def _on_signed_in(self, status: dict) -> None:
        self._signin_worker = None
        self.signin_btn.setEnabled(True)
        self.signin_btn.hide()
        acct = status.get("account") or ""
        sep = "：" if current_language() == "zh" else ": "
        self.error.setText(t("msg.signed_in", acct=f"{sep}{acct}" if acct else ""))
        # Warm the client/token so the next recording is instant.
        if self.backend == "azure" or self.polish_engine == "azure":
            import threading

            from . import azure_client

            threading.Thread(target=azure_client.warmup, daemon=True).start()

    def _on_signin_failed(self, message: str) -> None:
        self._signin_worker = None
        self.signin_btn.setEnabled(True)
        self.signin_btn.setText(t("btn.signin_retry"))
        self.signin_btn.show()
        self.error.setText(t("msg.signin_failed", message=message))

    # --- Local model download -------------------------------------------------
    def _download_selected_model(self) -> None:
        worker = getattr(self, "_model_worker", None)
        if worker is not None and worker.isRunning():
            return
        model_editor = self._settings_editors.get("model")
        hf_editor = self._settings_editors.get("hf_endpoint")
        model_name = (
            model_editor.currentText().strip() if isinstance(model_editor, QComboBox) else ""
        )
        if not model_name:
            self.error.setText(t("msg.pick_model_first"))
            return
        hf_endpoint = (
            hf_editor.text().strip() if isinstance(hf_editor, QLineEdit) else ""
        ) or DEFAULT_HF_ENDPOINT
        btn = self._settings_rows.get("_download_model")
        if isinstance(btn, QPushButton):
            btn.setEnabled(False)
            btn.setText(t("btn.downloading_model", name=model_name))
        self.error.setText(t("msg.downloading_model", name=model_name))
        worker = ModelDownloadWorker(model_name, hf_endpoint)
        worker.done.connect(self._on_model_downloaded)
        worker.failed.connect(self._on_model_download_failed)
        worker.finished.connect(lambda w=worker: self._discard_worker(w))
        self._model_worker = worker
        worker.start()

    def _reset_download_button(self) -> None:
        btn = self._settings_rows.get("_download_model")
        if isinstance(btn, QPushButton):
            btn.setEnabled(True)
            btn.setText(t("btn.download_model"))

    def _on_model_downloaded(self, model_name: str, path: str) -> None:
        self._model_worker = None
        self._reset_download_button()
        self.error.setText(t("msg.model_ready", name=model_name, path=path))

    def _on_model_download_failed(self, message: str) -> None:
        self._model_worker = None
        self._reset_download_button()
        self.error.setText(t("msg.model_failed", message=message))

    def _paste_text(self, text: str, target: "FocusTarget | None" = None) -> None:
        if target is None:
            target = self._recording_target or self._preferred_target
        pyperclip.copy(text)
        svc = get_platform_services()
        svc.restore_focus(target)  # type: ignore[arg-type]
        svc.paste_keystroke(submit=self.submit_to_active_app)
        self.enforce_topmost()

    # --- Stage accent palette -------------------------------------------------
    # The pet body colour is STABLE; these hues only tint the collapsed speech-bubble
    # accent and mirror PetOrb's per-stage glow. recording=red vs error=amber are
    # deliberately distinct hues so the two never read as the same state.
    _STAGE_COLORS = _style.STAGE_COLORS

    def _set_stage(self, stage: str) -> None:
        self.status.setText(stage.replace("_", " ").upper())
        self._stage = stage
        # PetOrb owns all pet visuals: it maps the stage to a distinct spring-animated
        # motion (idle breath / recording heartbeat / thinking wobble / done hop /
        # error shake) plus a stable body colour with a per-stage glow and particle
        # accents. VoiceDesktop just forwards the stage.
        self.orb.set_stage(stage)
        # The dangling cord streams energy dots toward the app while capturing, and
        # gets a little kick when capture starts or a result lands, so the connector
        # feels alive rather than static.
        badge = getattr(self, "_badge", None)
        if badge is not None:
            badge.set_flowing(stage in ("recording", "streaming"))
            if stage in ("recording", "done"):
                badge.nudge(2.5)

    def _install_topmost_guard(self) -> None:
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(1000)
        self._topmost_timer.timeout.connect(self.enforce_topmost)
        self._topmost_timer.start()
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(500)
        self._focus_timer.timeout.connect(self._remember_focus_target)
        self._focus_timer.start()
        self._install_hotkey_watchdog()
        # Keep the Azure AAD token warm so a recording never blocks on a fresh login
        # round-trip. The token lives ~60-90 min; refresh well inside that window.
        if self.backend == "azure" or self.polish_engine == "azure":
            self._token_timer = QTimer(self)
            self._token_timer.setInterval(20 * 60 * 1000)  # every 20 minutes
            self._token_timer.timeout.connect(self._refresh_azure_token)
            self._token_timer.start()

    def _refresh_azure_token(self) -> None:
        import threading

        from . import azure_client

        threading.Thread(target=azure_client.refresh_token, daemon=True).start()

    def enforce_topmost(self) -> None:
        if not self.isVisible():
            return
        # Don't re-assert topmost while a popup (combo-box dropdown, menu, etc.) is
        # open: SetWindowPos(HWND_TOPMOST) on our window re-stacks it above the
        # popup and dismisses it, which made dropdowns impossible to use.
        if QApplication.activePopupWidget() is not None:
            return
        get_platform_services().enforce_topmost(int(self.winId()))

    def _remember_focus_target(self) -> None:
        # Pause focus/context polling while the user is interacting with a popup
        # (e.g. picking a value in a settings combo box) so the UI doesn't churn.
        if QApplication.activePopupWidget() is not None:
            return
        info = get_platform_services().get_frontmost_window(int(self.winId()))
        if info is not None:
            title = ""
            try:
                title = focus_context.window_title(info.hwnd)
            except BaseException:
                title = ""
            target = FocusTarget(
                system=platform.system(),
                name=info.name,
                bundle_id=info.bundle_id,
                pid=info.pid,
                hwnd=info.hwnd,
                exe_path=info.exe_path,
                title=title,
            )
            # Carry over the previous deep-enriched context for the SAME window so
            # the panel doesn't flicker back to a bare title between deep probes.
            prev = self._preferred_target
            if prev is not None and prev.hwnd == target.hwnd and prev.title == target.title:
                target = replace(
                    target,
                    sub_kind=prev.sub_kind or target.sub_kind,
                    content=prev.content or target.content,
                    session=prev.session or target.session,
                    copilot_cli=prev.copilot_cli or target.copilot_cli,
                    plugins=prev.plugins or target.plugins,
                )
            self._preferred_target = self._light_enrich(target)
            self._refresh_live_transcript()
            self._schedule_live_enrich(self._preferred_target)
        # Keep the expanded 'Active Context' panel in sync with the live app.
        if not self._collapsed:
            self._refresh_context_panel()

    def _refresh_live_transcript(self) -> None:
        """Cheaply keep the Copilot CLI transcript current on the LIVE target.

        The transcript grows as the conversation advances even when the focused
        window/title never changes, so the (throttled, window-change-gated) deep
        UIA enrich would otherwise leave the panel frozen at the turns captured
        when the overlay opened. The transcript only needs the already-resolved
        session id, so we re-read just the recent turns (a small indexed DB query)
        and swap the ``copilot_cli`` plugin result in place. Throttled and fully
        guarded; never runs the expensive focus walk."""
        target = self._preferred_target
        if target is None:
            return
        plugins = list(target.plugins or ())
        if not any(getattr(p, "name", "") == "copilot_cli" for p in plugins):
            return  # not a Copilot pane (or not yet detected) — nothing to refresh
        sess = getattr(target, "session", None)
        sid = getattr(sess, "id", "") if sess else ""
        if not sid:
            return
        now = time.monotonic()
        if (now - self._live_transcript_ts) < 1.2:
            return
        self._live_transcript_ts = now
        try:
            from .plugins_catalog.copilot_cli import PLUGIN as _cop

            fresh = _cop.build_from_session(sid)
        except BaseException:
            return
        if fresh is None:
            return
        new_plugins = tuple(
            fresh if getattr(p, "name", "") == "copilot_cli" else p for p in plugins
        )
        # Only touch state / repaint when the transcript text actually changed.
        old = next((p for p in plugins if getattr(p, "name", "") == "copilot_cli"), None)
        if old is not None and getattr(old, "text", "") == fresh.text:
            return
        self._preferred_target = replace(target, plugins=new_plugins)
        if not self._collapsed:
            self._refresh_context_panel()
        elif self.polish != "off":
            self._update_context_view()

    def _schedule_live_enrich(self, target: FocusTarget | None) -> None:
        """Kick off a background UIA deep-enrich for the LIVE panel when the focused
        window/title changes (or periodically while expanded, to catch new chat
        messages). Throttled to one worker at a time so the 500ms poller never
        stalls on a slow accessibility walk."""
        if target is None or not target.hwnd:
            return
        if self._live_ctx_worker is not None and self._live_ctx_worker.isRunning():
            return
        key = (target.hwnd, target.title)
        expanded = not self._collapsed
        changed = key != self._live_ctx_key
        # Always re-probe on focus/title change. Otherwise re-probe the SAME window
        # periodically to catch things that change WITHOUT the hwnd/title changing:
        #  - the focused *pane* (Copilot terminal ⇄ plain terminal ⇄ editor all live
        #    in one VS Code window with one title), which decides the polish
        #    category — so we must re-detect even while collapsed for VS Code;
        #  - newly-arrived chat/terminal output while the panel is expanded.
        if not changed:
            vscode = self._looks_like_vscode(target)
            if not expanded and not vscode:
                return
            period = 1.5 if vscode else 2.5
            if (time.monotonic() - self._live_ctx_ts) < period:
                return
        self._live_ctx_key = key
        self._live_ctx_ts = time.monotonic()
        worker = LiveContextWorker(
            target.system, target.hwnd, target.exe_path, target.name
        )
        worker.ready.connect(self._on_live_context)
        self._live_ctx_worker = worker
        worker.start()

    def _on_live_context(self, hwnd: int, info: object) -> None:
        """Merge a background deep-enrich result into the live preferred target."""
        target = self._preferred_target
        if target is None or target.hwnd != hwnd or info is None:
            return
        if getattr(info, "is_empty", True):
            return
        self._preferred_target = replace(
            target,
            title=getattr(info, "title", "") or target.title,
            sub_kind=getattr(info, "sub_kind", "") or target.sub_kind,
            content=getattr(info, "content", "") or target.content,
            session=getattr(info, "session", None) or target.session,
            copilot_cli=getattr(info, "copilot_cli", False),
            plugins=tuple(getattr(info, "plugins", ()) or ()),
        )
        if not self._collapsed:
            self._refresh_context_panel()
        elif self.polish != "off":
            self._update_context_view()

    def _light_enrich(self, target: FocusTarget) -> FocusTarget:
        """Cheap enrichment for the LIVE panel (no UIA tree walk): resolve the
        Copilot CLI session for a focused VS Code window from its title alone, so
        the user sees the current session before recording. Runs only when the
        window title changed, and never raises."""
        title = (target.title or "").strip()
        if not title or not self._looks_like_vscode(target):
            self._light_session_title = ""
            return target
        if title == self._light_session_title and target.session is not None:
            return target
        self._light_session_title = title
        try:
            from . import copilot_session, focus_context as fc

            match = copilot_session.resolve_session(title, title)
        except BaseException:
            return target
        if match is None or match.is_empty:
            return target
        session = fc.SessionInfo(
            id=match.id,
            summary=match.summary,
            repository=match.repository,
            branch=match.branch,
            cwd=match.cwd,
            exact=match.exact,
        )
        return replace(target, session=session)

    @staticmethod
    def _looks_like_vscode(target: FocusTarget) -> bool:
        blob = f"{target.name} {target.exe_path} {target.title}".lower()
        return any(
            k in blob
            for k in ("visual studio code", "code.exe", "code - oss", "vscodium", "cursor")
        )


def _load_app_icon() -> QIcon | None:
    """Locate bb.ico across dev + PyInstaller-frozen layouts and return a QIcon."""
    import sys

    names = ("bb.ico",)
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates += [Path(meipass) / n for n in names]
    exe_dir = Path(sys.executable).resolve().parent
    candidates += [exe_dir / n for n in names]
    repo_root = Path(__file__).resolve().parents[2]
    candidates += [repo_root / "packaging" / n for n in names]
    for c in candidates:
        try:
            if c.is_file():
                icon = QIcon(str(c))
                if not icon.isNull():
                    return icon
        except Exception:
            pass
    return None


def run_qt_overlay(
    *,
    hotkey: str,
    language: str,
    model_name: str,
    backend: str,
    mlx_model: str,
    paste_to_active_app: bool | None,
    submit_to_active_app: bool | None,
    copy_to_clipboard: bool | None = None,
    hf_endpoint: str = DEFAULT_HF_ENDPOINT,
    replacement_pairs: list[str] | None = None,
    replacements_file: Path | None = None,
    polish: str = "off",
    context_file: Path | None = None,
    session_context: bool = False,
    language_preference: str = "zh-en",
    polish_engine: str = "rules",
    ollama_model: str = "qwen3:latest",
) -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Bubble Buddy")
    app.setApplicationDisplayName("Bubble Buddy")
    # Windowed app: keep child console programs (az.cmd, pwsh, ollama) from
    # flashing a black console window when this runs from source too.
    from .platform_services import suppress_child_console_windows

    suppress_child_console_windows()
    set_language(_config.load_config().get("ui_language"))
    # Refresh the OS autostart entry so it points at the current executable path
    # (e.g. after a reinstall or move) whenever the setting is enabled.
    try:
        from .platform_services import get_platform_services as _gps

        if _config_get_bool(_config.load_config(), "launch_at_startup"):
            _gps().set_launch_at_startup(True)
    except Exception:  # noqa: BLE001
        pass
    _icon = _load_app_icon()
    if _icon is not None:
        app.setWindowIcon(_icon)

    # Single-instance guard: if another overlay is already listening on our local
    # socket, ask it to surface itself and exit — this prevents duplicate orbs and
    # conflicting global hotkey listeners from accidental repeat launches.
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    _single_key = "bubble-buddy-overlay"
    _probe = QLocalSocket()
    _probe.connectToServer(_single_key)
    if _probe.waitForConnected(300):
        _probe.write(b"show")
        _probe.flush()
        _probe.waitForBytesWritten(500)
        _probe.disconnectFromServer()
        print(
            "Bubble Buddy is already running; surfaced the existing window.",
            flush=True,
        )
        return
    _probe.abort()
    QLocalServer.removeServer(_single_key)  # clear a stale socket left by a crash
    _instance_server = QLocalServer()
    _instance_server.listen(_single_key)

    try:
        _config.ensure_polish_categories_persisted()
    except Exception:  # noqa: BLE001
        pass
    widget = VoiceDesktop(
        hotkey=hotkey,
        language=language,
        model_name=model_name,
        backend=backend,
        mlx_model=mlx_model,
        paste_to_active_app=paste_to_active_app,
        submit_to_active_app=submit_to_active_app,
        copy_to_clipboard=copy_to_clipboard,
        hf_endpoint=hf_endpoint,
        replacement_pairs=replacement_pairs or [],
        replacements_file=replacements_file,
        polish=polish,
        context_file=context_file,
        session_context=session_context,
        language_preference=language_preference,
        polish_engine=polish_engine,
        ollama_model=ollama_model,
    )

    # When a second launch pings our socket, surface this window instead.
    def _on_second_instance() -> None:
        conn = _instance_server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(conn.readAll)
            widget._greet_second_instance()
            conn.disconnectFromServer()

    _instance_server.newConnection.connect(_on_second_instance)
    widget._instance_server = _instance_server  # keep a reference alive

    # On macOS, full-screen apps live in their own Space. Apply the native
    # collection behavior before the first show so the overlay is born as a
    # full-screen auxiliary window instead of being assigned to another Space.
    get_platform_services().enforce_topmost(int(widget.winId()))
    widget.show()
    if _config_get_bool(_config.load_config(), "start_collapsed"):
        QTimer.singleShot(0, widget._collapse)
    widget.raise_()
    widget.enforce_topmost()
    widget.start_hotkey()
    # First-launch welcome bubble (once): delayed so the orb is shown & positioned.
    QTimer.singleShot(900, widget._maybe_show_greeting)
    print("Qt desktop overlay shown. Press the configured hotkey or use the buttons.", flush=True)
    app.exec()
