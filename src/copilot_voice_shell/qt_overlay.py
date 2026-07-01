from __future__ import annotations

import platform
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from ctypes import c_void_p, wintypes
from pathlib import Path

import numpy as np
import pyperclip
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
from pynput import keyboard
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtCore import QTimer, QSize, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QBrush, QPolygonF, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QGraphicsDropShadowEffect,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import config as _config
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
            fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
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

        painter.setPen(QColor("#EAF0FB"))
        painter.setFont(self._font)
        text_rect = QRectF(
            body.x() + self.PAD_X,
            body.y() + self.PAD_Y,
            self._body_w - 2 * self.PAD_X,
            self._body_h - 2 * self.PAD_Y,
        )
        painter.drawText(
            text_rect,
            int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._text,
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
    ) -> None:
        super().__init__()
        self._raw = raw_text
        self._polish = polish
        self._context_file = context_file
        self._session_context = session_context
        self._language_preference = language_preference
        self._polish_engine = polish_engine
        self._ollama_model = ollama_model

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
        ("polish", "润色", "combo", ("off", "copilot")),
        ("polish_engine", "润色引擎", "combo", ("rules", "ollama", "azure")),
        ("ollama_model", "Ollama 模型", "text", ()),
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
        paste_to_active_app: bool,
        submit_to_active_app: bool,
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
        self.paste_to_active_app = paste_to_active_app
        self.submit_to_active_app = submit_to_active_app
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
        self.hotkey_listener: keyboard.GlobalHotKeys | None = None
        self._topmost_timer: QTimer | None = None
        self._focus_timer: QTimer | None = None
        self._preferred_target: FocusTarget | None = None
        self._recording_target: FocusTarget | None = None

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

        self.orb = QLabel("•ᴗ•")
        self.orb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.orb.setFixedSize(132, 132)
        self.orb.setFont(QFont("Segoe UI", 30, QFont.Weight.Bold))
        orb_shadow = QGraphicsDropShadowEffect(self.orb)
        orb_shadow.setBlurRadius(24)
        orb_shadow.setColor(QColor(0, 0, 0, 160))
        orb_shadow.setOffset(0, 3)
        self.orb.setGraphicsEffect(orb_shadow)

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
        self.transcript = QTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("Waiting for speech…")
        self.transcript.setFixedHeight(70)

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
        self.polished = QTextEdit()
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
        details_layout.addLayout(polished_header)
        details_layout.addWidget(self.polished)
        details_layout.addWidget(self.error)

        self.settings_toggle = QPushButton("⚙ Settings  ▸")
        self.settings_toggle.setObjectName("settingsToggle")
        self.settings_panel = self._build_settings_panel()
        self.settings_panel.hide()

        card_layout = QVBoxLayout()
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(8)
        card_layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.status)
        card_layout.addWidget(self.tip)
        card_layout.addLayout(top_buttons)
        card_layout.addWidget(self.details)
        card_layout.addWidget(self.settings_toggle)
        card_layout.addWidget(self.settings_panel)

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
        self._bubble_timer.timeout.connect(self._bubble.hide)

    def _show_bubble(self, text: str, *, final: bool = False) -> None:
        """Show/update the orb bubble with ``text``. While still transcribing
        (``final=False``) it stays up longer; the final polished text lingers a few
        seconds before dismissing. Only shown when collapsed to the orb."""
        text = (text or "").strip()
        if not text or not self._collapsed:
            return
        self._bubble.set_text(text)
        self._position_bubble()
        self._bubble.show()
        self._bubble.raise_()
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
        self._bubble.hide()

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
        }
        QPushButton#settingsToggle {
            text-align: left;
            background-color: #101B30;
            color: #9EB0E0;
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
        for widget in (
            self.status,
            self.tip,
            self.start_button,
            self.stop_button,
            self.shrink_button,
            self.settings_toggle,
            self.settings_panel,
            self.quit_button,
            self.details,
        ):
            widget.hide()
        self.card.setStyleSheet("#card { background: transparent; border: none; }")
        self._orb_radius = 44
        self.orb.setFixedSize(88, 88)
        self.orb.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        self._set_stage(self._stage)
        self.setMinimumSize(0, 0)
        self.adjustSize()
        self.resize(self.minimumSizeHint())

    def _expand(self) -> None:
        self._collapsed = False
        self._hide_bubble()
        for widget in (
            self.status,
            self.tip,
            self.start_button,
            self.stop_button,
            self.shrink_button,
            self.settings_toggle,
            self.quit_button,
            self.details,
        ):
            widget.show()
        self.settings_panel.setVisible(self._settings_open)
        self.card.setStyleSheet("")
        self.setStyleSheet(self._stylesheet())
        self._orb_radius = 66
        self.orb.setFixedSize(132, 132)
        self.orb.setFont(QFont("Segoe UI", 30, QFont.Weight.Bold))
        self._set_stage(self._stage)
        self.setMinimumWidth(360)
        self.adjustSize()

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
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        was_click = self._drag_offset is not None and not self._moved
        self._drag_offset = None
        if was_click and self._collapsed:
            self._expand()

    def start_hotkey(self) -> None:
        self.hotkey_listener = keyboard.GlobalHotKeys({normalize_hotkey(self.hotkey): self.hotkey_pressed.emit})
        self.hotkey_listener.start()

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
                value = _config_get(cfg, key)
                if kind == "combo":
                    editor: QWidget = QComboBox()
                    editor.addItems(list(options))
                    if value and value not in options:
                        editor.addItem(value)
                    editor.setCurrentText(value or (options[0] if options else ""))
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

        # Re-evaluate field visibility when backend / polish engine change.
        for key in ("backend", "polish_engine"):
            editor = self._settings_editors.get(key)
            if isinstance(editor, QComboBox):
                editor.currentTextChanged.connect(lambda _=None: self._update_field_visibility())

        self.save_settings_button = QPushButton("Save")
        self.save_settings_button.clicked.connect(self._save_settings)
        outer.addWidget(self.save_settings_button)

        self._update_field_visibility()
        return panel

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

    def _fit_height(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self.resize(self.width(), self.sizeHint().height())

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
            elif isinstance(editor, QLineEdit):
                editor.setText(value)
        self._update_field_visibility()

    def _collect_settings(self) -> dict:
        updates: dict = {}
        for key, editor in self._settings_editors.items():
            if isinstance(editor, QComboBox):
                value: str = editor.currentText().strip()
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
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        if self._topmost_timer is not None:
            self._topmost_timer.stop()
        if self._focus_timer is not None:
            self._focus_timer.stop()
        event.accept()

    def toggle_recording(self) -> None:
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

    def start_recording(self) -> None:
        try:
            self._hide_bubble()
            self._recording_target = self._preferred_target or self._current_focus_target()
            if self._use_realtime_stream():
                self._start_realtime_stream()
                self._start_max_record_timer()
                return
            self.recorder.start()
            self._start_max_record_timer()
            self._set_stage("recording")
            self.error.setText("Recording...")
        except BaseException as exc:  # noqa: BLE001
            self._set_stage("error")
            self.error.setText(f"Start failed: {exc}")

    def _start_realtime_stream(self) -> None:
        from . import config as _cfg
        from . import azure_client
        from .cli import build_azure_prompt, load_replacements

        azure = _cfg.get_azure_config()
        lang_hint = azure_client.transcribe_language_hint(self.language_preference)
        replacement_map = load_replacements(self.replacements_file, self.replacement_pairs)
        prompt = build_azure_prompt(replacement_map)

        self.transcript.clear()
        self.polished.clear()
        self.stream_worker = RealtimeStreamWorker(azure, lang_hint, prompt)
        self.stream_worker.partial.connect(self._on_stream_partial)
        self.stream_worker.finished_text.connect(self._on_stream_finished)
        self.stream_worker.failed.connect(self._on_failed)
        self.stream_worker.start()
        self._set_stage("recording")
        self.error.setText("Streaming (realtime)…")

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
            self.error.setText(f"Transcribing {audio_path.name}...")
            self.worker = TranscribeWorker(
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
            )
            self.worker.finished_text.connect(self._on_transcribed)
            self.worker.failed.connect(self._on_failed)
            self.worker.start()
        except BaseException as exc:  # noqa: BLE001
            self._set_stage("error")
            self.error.setText(f"Stop failed: {exc}")

    def _on_stream_partial(self, text: str) -> None:
        self._set_stage("streaming")
        self.transcript.setPlainText(text)
        self._show_bubble(text)

    def _on_stream_finished(self, raw_text: str) -> None:
        self.stream_worker = None
        self.transcript.setPlainText(raw_text)
        if not raw_text.strip():
            self._set_stage("error")
            self.error.setText("No speech captured.")
            return
        self._show_bubble(raw_text)
        self._set_stage("transcribing")
        self.error.setText("Polishing…")
        self.polish_worker = PolishWorker(
            raw_text,
            self.polish,
            self.context_file,
            self.session_context,
            self.language_preference,
            self.polish_engine,
            self.ollama_model,
        )
        self.polish_worker.finished_text.connect(self._on_transcribed)
        self.polish_worker.start()

    def _on_transcribed(self, raw_text: str, polished: str) -> None:
        self.transcript.setPlainText(raw_text)
        self.polished.setPlainText(polished or raw_text)
        self._set_stage("done")
        self.error.setText("Done.")
        self._show_bubble(polished or raw_text, final=True)
        if self.paste_to_active_app or self.submit_to_active_app:
            self._paste_text(polished or raw_text)

    def _on_failed(self, message: str) -> None:
        self._set_stage("error")
        self.error.setText(message)

    def _paste_text(self, text: str) -> None:
        pyperclip.copy(text)
        controller = keyboard.Controller()
        modifier = keyboard.Key.cmd if platform.system() == "Darwin" else keyboard.Key.ctrl
        # The overlay is a Tool window with WA_ShowWithoutActivating, so it never
        # holds keyboard focus. Just move the target app to the foreground and paste
        # into it — no need to hide/show the window (which caused a visible flicker).
        self._restore_focus_target(self._recording_target or self._preferred_target)
        time.sleep(0.2)
        with controller.pressed(modifier):
            controller.press("v")
            controller.release("v")
        if self.submit_to_active_app:
            time.sleep(0.1)
            controller.press(keyboard.Key.enter)
            controller.release(keyboard.Key.enter)
        self.enforce_topmost()

    def _set_stage(self, stage: str) -> None:
        colors = {
            "idle": "#6EA8FC",
            "recording": "#FF5C73",
            "loading_model": "#B59CFA",
            "streaming": "#78D6FA",
            "transcribing": "#FFD166",
            "transcribed": "#FFD166",
            "done": "#57CC99",
            "error": "#FF6B6B",
        }
        faces = {
            "idle": "•ᴗ•",
            "recording": "●ᴗ●",
            "loading_model": "•◡•",
            "streaming": "•⌄•",
            "transcribing": "•…•",
            "transcribed": "•…•",
            "done": "•‿•",
            "error": "•︵•",
        }
        self.status.setText(stage.replace("_", " ").upper())
        self.orb.setText(faces.get(stage, "•ᴗ•"))
        self._stage = stage
        self.orb.setStyleSheet(
            f"border-radius: {self._orb_radius}px;"
            f"background-color: {colors.get(stage, '#6EA8FC')};"
            "border: none;"
            "color: #09111f;"
        )

    def _install_topmost_guard(self) -> None:
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(1000)
        self._topmost_timer.timeout.connect(self.enforce_topmost)
        self._topmost_timer.start()
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(500)
        self._focus_timer.timeout.connect(self._remember_focus_target)
        self._focus_timer.start()

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
            self._preferred_target = target

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

                hwnd = int(ctypes.windll.user32.GetForegroundWindow())
                if hwnd == int(self.winId()) or hwnd == 0:
                    return None
                return FocusTarget(system=system, hwnd=hwnd)
            except BaseException:
                return None
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
    paste_to_active_app: bool,
    submit_to_active_app: bool,
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
    widget = VoiceDesktop(
        hotkey=hotkey,
        language=language,
        model_name=model_name,
        backend=backend,
        mlx_model=mlx_model,
        paste_to_active_app=paste_to_active_app,
        submit_to_active_app=submit_to_active_app,
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
