from __future__ import annotations

import platform
import os
import math
import re
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from ctypes import c_void_p, wintypes
from pathlib import Path

import numpy as np
import pyperclip
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
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
from .cli import (
    DEFAULT_HF_ENDPOINT,
    apply_replacements,
    load_replacements,
    merge_segment_text,
    normalize_hotkey,
    transcribe_audio_mlx,
    polish_text,
)


SAMPLE_RATE = 16_000


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


def _session_line(session: object) -> str:
    """Format a resolved Copilot CLI session for the context panel/prompt."""
    if session is None:
        return ""
    summary = (getattr(session, "summary", "") or "").strip()
    repo = (getattr(session, "repository", "") or "").strip()
    branch = (getattr(session, "branch", "") or "").strip()
    if not (summary or repo):
        return ""
    label = summary or "(未命名会话)"
    meta = []
    if repo:
        meta.append(repo)
    if branch:
        meta.append(branch)
    tail = f"（{' · '.join(meta)}）" if meta else ""
    hint = "" if getattr(session, "exact", False) else "≈"
    return f"当前会话：{hint}{label}{tail}"


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
            audio_path = Path(tempfile.gettempdir()) / "copilot-voice-shell" / f"qt-recording-{int(time.time())}.wav"
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
            if self.backend == "mlx":
                result = transcribe_audio_mlx(
                    self.audio_path,
                    self.language,
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
                model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                segments, _info = model.transcribe(str(self.audio_path), language=self.language)
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

    PAD_X = 13
    PAD_Y = 9
    TAIL_W = 18
    TAIL_H = 12
    RADIUS = 13
    MAX_TEXT_W = 260
    SHADOW = 16  # transparent margin reserved around the shape for the drop shadow

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
        self._font = QFont("Segoe UI", 10)
        self._body_w = 0
        self._body_h = 0
        self._accent: QColor | None = None

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(22)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 3)
        self.setGraphicsEffect(shadow)

    def set_text(self, text: str) -> None:
        """Update the bubble text and recompute its size from font metrics."""
        self._text = text or ""
        fm = QFontMetrics(self._font)
        flags = int(Qt.TextFlag.TextWordWrap)
        rect = fm.boundingRect(0, 0, self.MAX_TEXT_W, 10_000, flags, self._text)
        text_w = min(max(rect.width(), 1), self.MAX_TEXT_W)
        text_h = max(rect.height(), fm.height())
        self._body_w = text_w + 2 * self.PAD_X
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

    def _body_origin(self) -> QPointF:
        """Top-left of the body rect within the widget (accounting for tail + shadow margin)."""
        m = self.SHADOW
        left = m + (self.TAIL_H if self._tail_side == "left" else 0)
        top = m + (self.TAIL_H if self._tail_side == "top" else 0)
        return QPointF(left, top)

    def tail_tip_global(self) -> QPointF:
        """Global position of the tail tip (the point that touches the orb)."""
        o = self._body_origin()
        cx = o.x() + self._body_w / 2
        cy = o.y() + self._body_h / 2
        if self._tail_side == "bottom":
            local = QPointF(cx, o.y() + self._body_h + self.TAIL_H)
        elif self._tail_side == "top":
            local = QPointF(cx, o.y() - self.TAIL_H)
        elif self._tail_side == "left":
            local = QPointF(o.x() - self.TAIL_H, cy)
        else:  # right
            local = QPointF(o.x() + self._body_w + self.TAIL_H, cy)
        return self.mapToGlobal(local.toPoint())

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
        self._accent = QColor(color) if color else None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

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
            body.x() + self.PAD_X + (5 if self._tail_side != "right" and self._accent else 0),
            body.y() + self.PAD_Y,
            self._body_w - 2 * self.PAD_X - (5 if self._accent else 0),
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

        # NOTE: intentionally NO QGraphicsDropShadowEffect here. On a translucent
        # frameless top-level window that effect can blank the widget's content on
        # activation-driven repaints (the icon "disappears" when switching apps), so
        # we paint a soft shadow manually in paintEvent instead.

        m = self.SHADOW
        w = self.BADGE_D + 2 * m
        h = self.CORD_LEN + self.BADGE_D + self.LABEL_H + 2 * m
        self.resize(w, h)

    def set_context(self, *, color: str, pixmap: QPixmap | None, letter: str, label: str) -> None:
        self._color = QColor(color)
        self._pixmap = pixmap
        self._letter = (letter or "?")[:1].upper()
        self._label = label or ""
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

    def _coil_path(self, cx: float, y0: float, y1: float) -> QPainterPath:
        """A stretched-helix path between y0 and y1 that reads as a coiled spring /
        telephone cord. Modeled as x = amp·sin(θ) with a slight perspective squash so
        successive loops look 3D rather than a flat zig-zag."""
        path = QPainterPath()
        span = max(y1 - y0, 1.0)
        steps = 96
        amp = self.COIL_AMP
        for i in range(steps + 1):
            t = i / steps
            theta = t * self.COIL_TURNS * 2 * math.pi
            # ease the amplitude in/out so the coil tapers into the endpoints
            taper = math.sin(min(t, 1 - t) * math.pi) ** 0.5 if 0 < t < 1 else 0.0
            x = cx + amp * taper * math.sin(theta)
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
        cx = self.width() / 2
        r = self.BADGE_D / 2
        badge_cy = m + self.CORD_LEN + r
        badge_center = QPointF(cx, badge_cy)

        # Coiled "telephone cord" spring from just under the orb to the badge top.
        coil = self._coil_path(cx, m + 2, badge_cy - r - 1)
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


# Settings grouped into collapsible categories. Each field: (key, label, kind, options)
# kind: "text" -> QLineEdit; "combo" -> QComboBox with the given options.
_SETTINGS_CATEGORIES: list[tuple[str, list[tuple[str, str, str, tuple[str, ...]]]]] = [
    ("常规 General", [
        ("language_preference", "语言偏好", "combo", ("zh-en", "zh", "en")),
        ("language", "语言提示", "text", ()),
        ("hotkey", "热键", "text", ()),
        ("max_record_seconds", "最大收听秒数 (0=不限)", "text", ()),
    ]),
    ("转写 Transcription", [
        ("backend", "后端", "combo", ("faster-whisper", "mlx", "azure")),
        ("model", "本地 Whisper 模型", "text", ()),
        ("hf_endpoint", "HF endpoint", "text", ()),
        ("mlx_model", "MLX 模型", "text", ()),
    ]),
    ("润色 Polish", [
        ("polish", "润色", "combo", ("off", "auto", "copilot", "dev", "im", "notes", "email", "browser")),
        ("polish_engine", "润色引擎", "combo", ("rules", "ollama", "azure")),
        ("ollama_model", "Ollama 模型", "text", ()),
    ]),
    ("输出 Output", [
        ("copy_to_clipboard", "复制到剪贴板", "toggle", ()),
        ("paste_to_active_app", "复制到光标", "toggle", ()),
        ("submit_to_active_app", "粘贴后回车提交", "toggle", ()),
    ]),
    ("线上模型 Azure", [
        ("azure.endpoint", "Endpoint", "text", ()),
        ("azure.api_version", "API version", "text", ()),
        ("azure.auth", "Auth", "combo", ("aad", "api_key")),
        ("azure.api_key", "API key (api_key 模式)", "text", ()),
        ("azure.transcribe_deployment", "转写部署", "text", ()),
        ("azure.transcribe_mode", "转写模式 Streaming", "combo", ("batch", "stream", "realtime")),
        ("azure.realtime_api_version", "Realtime API version", "text", ()),
        ("azure.chat_deployment", "对话部署", "text", ()),
    ]),
]


def _field_applies(key: str, backend: str, polish_engine: str) -> bool:
    """Whether a settings field is relevant given the current backend / polish engine.
    Local-model fields are hidden when an online (azure) backend is selected, etc."""
    if key in ("model", "hf_endpoint"):
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

    BODY = QColor("#6E9BFF")
    INK = QColor("#20304f")

    # app processing-stage -> one of the 5 visual states
    _VIS = {
        "idle": "idle",
        "recording": "recording",
        "loading_model": "thinking",
        # Realtime streaming is live voice INPUT (like recording), not processing:
        # share the recording visual so the expanding "sound-wave" ring ripples make
        # it obvious the user is still dictating, even when the overlay is collapsed.
        "streaming": "recording",
        "transcribing": "thinking",
        "transcribed": "thinking",
        "done": "done",
        "error": "error",
    }
    # state -> glow / accent colour (distinct hues; recording red vs error amber)
    _GLOW = {
        "idle": QColor("#6E9BFF"),
        "recording": QColor("#FF4D67"),
        "thinking": QColor("#B57CFF"),
        "done": QColor("#39D98A"),
        "error": QColor("#FFA426"),
    }
    DONE = QColor("#39D98A")
    RECORDING = QColor("#FF4D67")
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
        self._sGlow.set(0.16)
        if vis == "idle":
            self._sGlow.set(0.15)
        elif vis == "recording":
            self._heart_amp = 0.05
            self._breath_amp = 0.02
            self._sGlow.set(0.5)
            self._sMouth.set(0.14)
        elif vis == "thinking":
            self._breath_amp = 0.02
            self._sGlow.set(0.40)
            self._sMouth.set(0.0)
        elif vis == "done":
            self._sGlow.set(0.55)
            self._sMouth.set(0.9)
            self.hop()
            QTimer.singleShot(120, self.blink)
            self._sparkle()
        elif vis == "error":
            self._sGlow.set(0.5)
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
        # thinking: a calm vertical float (whole-body bob) + a slow breathing aura.
        # This reads as "processing" without rippling the silhouette (which looked
        # like the mouth was moving), so the face stays perfectly still.
        think_bob = math.sin(t * 2.3) * 0.05 * R if self._vis == "thinking" else 0.0
        if self._vis == "thinking":
            self._sGlow.set(0.30 + 0.16 * (0.5 + 0.5 * math.sin(t * 2.6)))
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
        eye_y = -R * 0.05
        eye_x = R * 0.32
        # Simple round dot eyes (no catchlight/gloss); blink squashes them to a line.
        ew = R * 0.12
        eh = ew * (1 - self._blink) + R * 0.015
        gx = self._gaze * R * 0.06
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
        my = R * 0.28
        half_w = R * 0.20
        ctrl_y = my + m * R * 0.40  # +y is downward: m>0 dips down (smile), m<0 lifts (frown)
        pen = QPen(self.INK, R * 0.055, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
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

        if self.backend == "azure" or self.polish_engine == "azure":
            import threading

            from . import azure_client

            threading.Thread(target=azure_client.warmup, daemon=True).start()

        self.setWindowTitle("Copilot Voice Sprite")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
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

        self.tip = QLabel(f"Hotkey: {hotkey}")
        self.tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tip.setObjectName("tip")

        self.start_button = QPushButton()
        self.start_button.setObjectName("iconbtn")
        self.start_button.setToolTip("Start recording")
        self.stop_button = QPushButton()
        self.stop_button.setObjectName("iconbtn")
        self.stop_button.setToolTip("Stop recording")
        self.shrink_button = QPushButton()
        self.shrink_button.setObjectName("iconbtn")
        self.shrink_button.setToolTip("Shrink to orb")
        self.quit_button = QPushButton()
        self.quit_button.setObjectName("iconbtn")
        self.quit_button.setToolTip("Quit")
        _apply_button_icon(self.start_button, "fa6s.microphone")
        _apply_button_icon(self.stop_button, "fa6s.stop")
        _apply_button_icon(self.shrink_button, "fa6s.compress")
        _apply_button_icon(self.quit_button, "fa6s.xmark")

        top_buttons = QHBoxLayout()
        top_buttons.setSpacing(10)
        top_buttons.addStretch(1)
        top_buttons.addWidget(self.start_button)
        top_buttons.addWidget(self.stop_button)
        top_buttons.addWidget(self.shrink_button)
        top_buttons.addWidget(self.quit_button)
        top_buttons.addStretch(1)

        raw_title = QLabel("Raw Transcript")
        raw_title.setObjectName("section")
        self.copy_raw_button = QPushButton("⧉")
        self.copy_raw_button.setObjectName("copy")
        self.copy_raw_button.setToolTip("Copy raw transcript")
        raw_header = QHBoxLayout()
        raw_header.setContentsMargins(0, 0, 0, 0)
        raw_header.addWidget(raw_title)
        raw_header.addStretch(1)
        raw_header.addWidget(self.copy_raw_button)
        self.transcript = ResizableTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("Waiting for speech…")
        self.transcript.setFixedHeight(70)

        context_title = QLabel("Active Context")
        context_title.setObjectName("section")
        self.context_badge_dot = QLabel("●")
        self.context_badge_dot.setObjectName("contextDot")
        context_header = QHBoxLayout()
        context_header.setContentsMargins(0, 0, 0, 0)
        context_header.addWidget(context_title)
        context_header.addStretch(1)
        context_header.addWidget(self.context_badge_dot)
        self.context_view = ResizableTextEdit()
        self.context_view.setReadOnly(True)
        self.context_view.setObjectName("contextView")
        self.context_view.setPlaceholderText("No app context detected yet.")
        self.context_view.setFixedHeight(60)

        polished_title = QLabel("Polished")
        polished_title.setObjectName("section")
        self.copy_polished_button = QPushButton("⧉")
        self.copy_polished_button.setObjectName("copy")
        self.copy_polished_button.setToolTip("Copy polished text")
        polished_header = QHBoxLayout()
        polished_header.setContentsMargins(0, 0, 0, 0)
        polished_header.addWidget(polished_title)
        polished_header.addStretch(1)
        polished_header.addWidget(self.copy_polished_button)
        self.polished = ResizableTextEdit()
        self.polished.setReadOnly(True)
        self.polished.setPlaceholderText("Waiting for polished text…")
        self.polished.setFixedHeight(70)

        self.error = QLabel("Ready.")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)

        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(4)
        details_layout.addLayout(raw_header)
        details_layout.addWidget(self.transcript)
        details_layout.addLayout(context_header)
        details_layout.addWidget(self.context_view)
        details_layout.addLayout(polished_header)
        details_layout.addWidget(self.polished)
        details_layout.addWidget(self.error)

        self.settings_toggle = QPushButton("⚙ Settings  ▸")
        self.settings_toggle.setObjectName("settingsToggle")
        self.settings_panel = self._build_settings_panel()
        self.settings_panel.hide()

        # Collapsible history of completed dictations. Because transcribe/polish now
        # run concurrently, each finished result is appended here so a new recording
        # never discards a previous one. Collapsed by default; expanded on demand.
        self._history: list[dict] = []
        self.history_toggle = QPushButton("🕘 History  ▸")
        self.history_toggle.setObjectName("settingsToggle")
        self.history_panel = QWidget()
        self.history_panel.setObjectName("historyPanel")
        self._history_layout = QVBoxLayout(self.history_panel)
        self._history_layout.setContentsMargins(4, 4, 4, 4)
        self._history_layout.setSpacing(4)
        self._history_empty = QLabel("No dictations yet.")
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
        self.copy_raw_button.clicked.connect(lambda: self._copy_field(self.transcript, "Raw"))
        self.copy_polished_button.clicked.connect(lambda: self._copy_field(self.polished, "Polished"))
        self.quit_button.clicked.connect(self.close)
        self.hotkey_pressed.connect(self.toggle_recording)
        self._max_record_timer = QTimer(self)
        self._max_record_timer.setSingleShot(True)
        self._max_record_timer.timeout.connect(self._on_max_record_timeout)
        self._build_bubble()
        self._set_stage("idle")
        self._install_topmost_guard()

    def _build_bubble(self) -> None:
        """A speech bubble shown near the orb while collapsed. It surfaces the live
        raw transcript, then the polished text, and auto-dismisses after a while so
        it never lingers on screen. Uses a custom-painted bubble with a tail that
        points at the orb and sizes correctly on first render."""
        self._bubble = SpeechBubble(self)
        self._bubble.hide()
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(lambda: self._fade_out(self._bubble))
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
        text = (text or "").strip()
        if not text or not self._collapsed:
            return
        was_hidden = not self._bubble.isVisible()
        self._bubble.set_text(text)
        self._bubble.set_accent(self._STAGE_COLORS.get(self._stage, "#6EA8FC"))
        self._position_bubble()
        self._reveal(self._bubble, animate=was_hidden)
        self._bubble_timer.stop()
        self._bubble_timer.start(9000 if final else 20000)

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
        self._fade_out(self._bubble)
        self._hide_badge()

    def _stylesheet(self) -> str:
        return """
        #card {
            background-color: rgba(10, 18, 33, 245);
            border: 1px solid #22314f;
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

        for title, fields in _SETTINGS_CATEGORIES:
            header = QPushButton(f"{title}  ▸")
            header.setObjectName("settingsToggle")
            header.setCheckable(False)
            body = QWidget()
            body_form = QFormLayout(body)
            body_form.setContentsMargins(8, 4, 4, 4)
            body_form.setSpacing(6)
            body_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            body.setVisible(False)

            for key, label, kind, options in fields:
                if kind == "note":
                    note = QLabel(label)
                    note.setObjectName("promptNote")
                    note.setWordWrap(True)
                    self._settings_rows[key] = note
                    body_form.addRow(note)
                    continue
                value = _config_get(cfg, key)
                if kind == "combo":
                    editor: QWidget = QComboBox()
                    editor.addItems(list(options))
                    if value and value not in options:
                        editor.addItem(value)
                    editor.setCurrentText(value or (options[0] if options else ""))
                elif kind == "toggle":
                    editor = QCheckBox()
                    editor.setChecked(_config_get_bool(cfg, key))
                elif kind == "multiline":
                    editor = QTextEdit()
                    editor.setObjectName("promptEdit")
                    editor.setPlainText(value)
                    editor.setFixedHeight(96)
                    editor.setAcceptRichText(False)
                    # Show the built-in default as a placeholder so an empty field
                    # means "use default" while the user still sees the baseline.
                    mode = key.split(".", 1)[1] if "." in key else ""
                    default_prompt = _polish_defaults().get(mode, "")
                    if default_prompt:
                        editor.setPlaceholderText(default_prompt.strip())
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

            header.clicked.connect(lambda _=False, b=body, h=header, t=title: self._toggle_section(b, h, t))
            self._settings_sections.append((header, body, title))
            outer.addWidget(header)
            outer.addWidget(body)

            if title == "润色 Polish":
                cat_header, cat_body = self._build_categories_section(cfg)
                outer.addWidget(cat_header)
                outer.addWidget(cat_body)

        # Re-evaluate field visibility when backend / polish engine change.
        for key in ("backend", "polish_engine"):
            editor = self._settings_editors.get(key)
            if isinstance(editor, QComboBox):
                editor.currentTextChanged.connect(lambda _=None: self._update_field_visibility())

        self.save_settings_button = QPushButton("Save")
        self.save_settings_button.clicked.connect(self._save_settings)
        outer.addWidget(self.save_settings_button)

        self._sync_polish_combo()
        self._update_field_visibility()
        return panel

    # ---- Polish categories (user-editable, full CRUD) -------------------------

    _CATEGORIES_TITLE = "分类管理 Categories"

    def _build_categories_section(self, cfg: dict) -> tuple[QPushButton, QWidget]:
        """Build the collapsible section that lets the user add / remove / edit the
        polish categories (label, color, app keywords, prompt)."""
        header = QPushButton(f"{self._CATEGORIES_TITLE}  ▸")
        header.setObjectName("settingsToggle")
        header.setCheckable(False)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 4, 4, 4)
        body_layout.setSpacing(6)
        body.setVisible(False)

        note = QLabel(
            "为每个场景（分类）自定义：显示名、颜色、匹配的 App 关键词（逗号分隔，"
            "auto 模式据此识别当前应用）、以及润色 Prompt。可新增或删除分类。\n"
            "Prompt 仅对 Ollama / Azure 润色引擎生效；关键词与颜色对所有引擎生效。"
        )
        note.setObjectName("promptNote")
        note.setWordWrap(True)
        body_layout.addWidget(note)

        # Container that holds one editable card per category.
        self._categories_container = QWidget()
        self._categories_layout = QVBoxLayout(self._categories_container)
        self._categories_layout.setContentsMargins(0, 0, 0, 0)
        self._categories_layout.setSpacing(8)
        body_layout.addWidget(self._categories_container)

        add_btn = QPushButton("➕ 新增分类 Add category")
        add_btn.setObjectName("addCategoryButton")
        add_btn.clicked.connect(self._add_blank_category)
        body_layout.addWidget(add_btn)

        header.clicked.connect(
            lambda _=False, b=body, h=header: self._toggle_section(b, h, self._CATEGORIES_TITLE)
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
        form.addRow("显示名 Label", label_edit)
        form.addRow("颜色 Color", color_edit)
        form.addRow("App 关键词", keywords_edit)
        form.addRow("润色 Prompt", prompt_edit)

        remove_btn = QPushButton("🗑 删除此分类 Remove")
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

    def _toggle_section(self, body: QWidget, header: QPushButton, title: str) -> None:
        show = not body.isVisible()
        body.setVisible(show)
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
        for header, body, title in self._settings_sections:
            keys = [k for k, _l, _kd, _o in dict(_SETTINGS_CATEGORIES)[title]]
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
        self.settings_toggle.setText("⚙ Settings  ▾" if self._settings_open else "⚙ Settings  ▸")
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
        self.history_toggle.setText(f"🕘 History{suffix}  {arrow}")

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
        copy_btn = QPushButton("Copy")
        copy_btn.setObjectName("historyCopy")
        copy_btn.setFixedWidth(52)
        copy_btn.clicked.connect(lambda _=False, t=text: self._copy_history_text(t))
        hl.addWidget(label, 1)
        hl.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _copy_history_text(self, text: str) -> None:
        try:
            pyperclip.copy(text)
            self.error.setText("Copied history item to clipboard.")
        except pyperclip.PyperclipException as exc:
            self.error.setText(f"Clipboard copy failed: {exc}")

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
            self.error.setText(f"Save settings failed: {exc}")
            return
        self.apply_settings(_config.load_config(reload=True))
        self.error.setText(f"Settings saved to {path.name}.")

    def _copy_field(self, edit: QTextEdit, label: str) -> None:
        text = edit.toPlainText().strip()
        if not text:
            self.error.setText(f"{label} is empty; nothing to copy.")
            return
        pyperclip.copy(text)
        self.error.setText(f"Copied {label} to clipboard.")

    def apply_settings(self, cfg: dict) -> None:
        """Apply saved config to the live overlay so changes take effect without a restart."""
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

        new_hotkey = cfg.get("hotkey", self.hotkey)
        if new_hotkey != self.hotkey:
            if self.hotkey_listener is not None:
                self.hotkey_listener.stop()
            self.hotkey = new_hotkey
            self.start_hotkey()
        self.tip.setText(f"Hotkey: {self.hotkey}")

        if self.backend == "azure" or self.polish_engine == "azure":
            from . import azure_client

            threading.Thread(target=azure_client.warmup, daemon=True).start()

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
            copilot_session=bool(target.session) if target else False,
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
            copilot_session=bool(target.session) if target else False,
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
            self.app_name_label.setText("未识别应用")
        self.app_name_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    _SUB_KIND_LABELS = {
        "terminal": "终端",
        "editor": "编辑器",
        "chat": "会话",
        "browser": "网页",
        "document": "文档",
    }

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
        sub = self._SUB_KIND_LABELS.get(target.sub_kind or "")
        if sub:
            parts.append(f"焦点区域：{sub}")
        content = (target.content or "").strip()
        if content:
            parts.append(f"焦点内容：{content}")
        session = _session_line(getattr(target, "session", None))
        if session:
            parts.append(session)
        return "；".join(parts)

    def _focus_detail_lines(self, target: FocusTarget | None) -> str:
        """Human-readable focus detail for the expanded 'Active Context' panel."""
        if target is None:
            return ""
        lines: list[str] = []
        title = (target.title or "").strip()
        if title:
            lines.append(f"窗口标题：{title}")
        sub = self._SUB_KIND_LABELS.get(target.sub_kind or "")
        if sub:
            lines.append(f"焦点区域：{sub}")
        content = (target.content or "").strip()
        if content:
            snippet = content if len(content) <= 300 else content[:300] + "…"
            lines.append(f"焦点内容：{snippet}")
        session = _session_line(getattr(target, "session", None))
        if session:
            lines.append(session)
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
            copilot_session=bool(target.session) if target else False,
        )
        color = polish_mode_color(mode)
        label = polish_mode_label(mode)
        header = f"{name or '未识别应用'} · {label}"
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
        sub = self._SUB_KIND_LABELS.get(target.sub_kind or "")
        title = (target.title or "").strip()
        if title:
            head = title if len(title) <= 60 else title[:60] + "…"
            lines.append(f"{sub}｜{head}" if sub else head)
        elif sub:
            lines.append(sub)
        content = (target.content or "").strip()
        if content:
            snippet = content if len(content) <= 90 else content[:90] + "…"
            lines.append(snippet)
        return "\n".join(lines)

    def _show_context_bubble(self) -> None:
        """Show the context bubble next to the badge (collapsed only)."""
        if not self._collapsed or not self._badge.isVisible():
            self._fade_out(self._context_bubble)
            return
        text = self._context_bubble_text()
        if not text:
            self._fade_out(self._context_bubble)
            return
        was_hidden = not self._context_bubble.isVisible()
        self._context_bubble.set_text(text)
        target = self._recording_target or self._preferred_target
        _mode, color, _name, _panel = self._context_for(target)
        self._context_bubble.set_accent(color)
        self._position_context_bubble()
        self._reveal(self._context_bubble, animate=was_hidden)

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
        self._fade_out(self._context_bubble)

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
            self._recording_target = self._current_focus_target() or self._preferred_target
            if self._use_realtime_stream():
                self._start_realtime_stream("")
                self._start_max_record_timer()
            else:
                self.recorder.start()
                self._start_max_record_timer()
                self._set_stage("recording")
                self.error.setText("Recording...")
            # Enrich context (window title, focused control, session) after capture
            # has begun; this updates the polish context, badge and status suffix.
            QTimer.singleShot(0, self._enrich_recording_context)
        except BaseException as exc:  # noqa: BLE001
            self._set_stage("error")
            self.error.setText(f"Start failed: {exc}")

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
            base = "Streaming (realtime)…" if streaming else "Recording..."
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
        self.error.setText(f"Streaming (realtime)…{status_suffix}")

    def stop_recording(self) -> None:
        try:
            self._max_record_timer.stop()
            if getattr(self, "stream_worker", None) is not None and self.stream_worker.isRunning():
                self._set_stage("transcribing")
                self.error.setText("Finishing…")
                self.stream_worker.stop()
                return
            audio_path = self.recorder.stop()
            self._set_stage("transcribing")
            job_target = self._recording_target
            app_desc = f" [{job_target.name}]" if job_target and job_target.name else ""
            self.error.setText(f"Transcribing {audio_path.name}{app_desc}...")
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
                copilot_session=bool(job_target.session) if job_target else False,
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
            self.error.setText(f"Stop failed: {exc}")

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
            self.error.setText("No speech captured.")
            return
        self._show_bubble(raw_text)
        self._set_stage("transcribing")
        desc = self._target_polish_desc()
        self.error.setText(f"Polishing…{f' · {desc}' if desc else ''}")
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
            copilot_session=bool(job_target.session) if job_target else False,
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
        self.error.setText("Done.")
        self._show_bubble(polished or raw_text, final=True)
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
                self.error.setText("Copied to clipboard.")
            except pyperclip.PyperclipException as exc:
                self.error.setText(f"Clipboard copy failed: {exc}")

    def _on_failed(self, message: str, worker: "QThread | None" = None) -> None:
        if worker is not None and self.stream_worker is worker:
            self.stream_worker = None
        self._discard_worker(worker)
        self._set_stage("error")
        self.error.setText(message)
        if self._badge.isVisible():
            self._badge_timer.start(3000)

    def _paste_text(self, text: str, target: "FocusTarget | None" = None) -> None:
        if target is None:
            target = self._recording_target or self._preferred_target
        pyperclip.copy(text)
        controller = keyboard.Controller()
        modifier = keyboard.Key.cmd if platform.system() == "Darwin" else keyboard.Key.ctrl
        # The overlay is a Tool window with WA_ShowWithoutActivating, so it never
        # holds keyboard focus. Just move the target app to the foreground and paste
        # into it — no need to hide/show the window (which caused a visible flicker).
        self._restore_focus_target(target)
        time.sleep(0.2)
        with controller.pressed(modifier):
            controller.press("v")
            controller.release("v")
        if self.submit_to_active_app:
            time.sleep(0.1)
            controller.press(keyboard.Key.enter)
            controller.release(keyboard.Key.enter)
        self.enforce_topmost()

    # --- Stage accent palette -------------------------------------------------
    # The pet body colour is STABLE; these hues only tint the collapsed speech-bubble
    # accent and mirror PetOrb's per-stage glow. recording=red vs error=amber are
    # deliberately distinct hues so the two never read as the same state.
    _STAGE_IDLE = "#6E9BFF"       # friendly periwinkle blue — at rest
    _STAGE_RECORDING = "#FF4D67"  # rose red — actively capturing (record convention)
    _STAGE_WORKING = "#B57CFF"    # lively violet — model / transcribe / stream
    _STAGE_DONE = "#39D98A"       # fresh mint green — success
    _STAGE_ERROR = "#FFA426"      # amber — failed (warning hue, NOT the recording red)

    _STAGE_COLORS = {
        "idle": _STAGE_IDLE,
        "recording": _STAGE_RECORDING,
        "loading_model": _STAGE_WORKING,
        "streaming": _STAGE_WORKING,
        "transcribing": _STAGE_WORKING,
        "transcribed": _STAGE_WORKING,
        "done": _STAGE_DONE,
        "error": _STAGE_ERROR,
    }

    def _set_stage(self, stage: str) -> None:
        self.status.setText(stage.replace("_", " ").upper())
        self._stage = stage
        # PetOrb owns all pet visuals: it maps the stage to a distinct spring-animated
        # motion (idle breath / recording heartbeat / thinking wobble / done hop /
        # error shake) plus a stable body colour with a per-stage glow and particle
        # accents. VoiceDesktop just forwards the stage.
        self.orb.set_stage(stage)

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
        self._enforce_native_topmost()

    def _enforce_native_topmost(self) -> None:
        system = platform.system()
        if system == "Darwin":
            self._enforce_macos_topmost()
        elif system == "Windows":
            self._enforce_windows_topmost()

    def _enforce_macos_topmost(self) -> None:
        try:
            import objc
            from AppKit import (
                NSScreenSaverWindowLevel,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorIgnoresCycle,
                NSWindowCollectionBehaviorStationary,
            )

            ns_view = objc.objc_object(c_void_p=int(self.winId()))
            ns_window = ns_view.window()
            if ns_window is None:
                return
            ns_window.setLevel_(NSScreenSaverWindowLevel)
            ns_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorIgnoresCycle
            )
            ns_window.orderFrontRegardless()
        except BaseException:
            return

    def _remember_focus_target(self) -> None:
        target = self._current_focus_target()
        if target is not None:
            self._preferred_target = self._light_enrich(target)
        # Keep the expanded 'Active Context' panel in sync with the live app.
        if not self._collapsed:
            self._refresh_context_panel()

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

    def _current_focus_target(self) -> FocusTarget | None:
        system = platform.system()
        if system == "Darwin":
            try:
                from AppKit import NSWorkspace

                app = NSWorkspace.sharedWorkspace().frontmostApplication()
                if app is None:
                    return None
                pid = int(app.processIdentifier())
                if pid == os.getpid():
                    return None
                return FocusTarget(
                    system=system,
                    bundle_id=app.bundleIdentifier() or "",
                    name=app.localizedName() or "",
                    pid=pid,
                )
            except BaseException:
                return None
        if system == "Windows":
            try:
                import ctypes
                from ctypes import wintypes

                hwnd = int(ctypes.windll.user32.GetForegroundWindow())
            except BaseException:
                return None
            if hwnd == 0 or hwnd == int(self.winId()):
                return None
            # Resolve the owning process for its name/icon. If this fails (protected
            # process, transient error) we STILL return a valid target with the hwnd
            # so the focus poller updates to the new app instead of keeping a stale
            # one — otherwise the badge could keep showing the previous app.
            name = ""
            exe_path = ""
            process_id = 0
            try:
                import ctypes
                from ctypes import wintypes

                pid = wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                process_id = int(pid.value)
                h_process = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
                if h_process:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(1024)
                    if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                        h_process, 0, buf, ctypes.byref(size)
                    ):
                        exe_path = buf.value
                        name = os.path.basename(exe_path)
                    ctypes.windll.kernel32.CloseHandle(h_process)
            except BaseException:
                pass
            return FocusTarget(
                system=system,
                hwnd=hwnd,
                pid=process_id,
                name=name,
                bundle_id=name,
                exe_path=exe_path,
                title=focus_context.window_title(hwnd),
            )
        return None

    def _restore_focus_target(self, target: FocusTarget | None) -> None:
        if target is None:
            return
        if target.system == "Darwin":
            try:
                import subprocess

                if target.bundle_id:
                    subprocess.run(
                        ["osascript", "-e", f'tell application id "{target.bundle_id}" to activate'],
                        check=False,
                    )
                elif target.name:
                    subprocess.run(["osascript", "-e", f'tell application "{target.name}" to activate'], check=False)
            except BaseException:
                return
        elif target.system == "Windows" and target.hwnd:
            try:
                import ctypes

                ctypes.windll.user32.SetForegroundWindow(wintypes.HWND(target.hwnd))
            except BaseException:
                return

    def _enforce_windows_topmost(self) -> None:
        try:
            import ctypes

            hwnd = wintypes.HWND(int(self.winId()))
            hwnd_topmost = wintypes.HWND(-1)
            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_noactivate = 0x0010
            ctypes.windll.user32.SetWindowPos(hwnd, hwnd_topmost, 0, 0, 0, 0, swp_nomove | swp_nosize | swp_noactivate)
        except BaseException:
            return


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
    widget.show()
    widget._collapse()
    widget.raise_()
    widget.enforce_topmost()
    widget.start_hotkey()
    print("Qt desktop overlay shown. Press the configured hotkey or use the buttons.", flush=True)
    app.exec()
