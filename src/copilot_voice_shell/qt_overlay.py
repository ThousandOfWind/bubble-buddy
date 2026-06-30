from __future__ import annotations

import platform
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pyperclip
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
from pynput import keyboard
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .cli import (
    DEFAULT_HF_ENDPOINT,
    apply_replacements,
    load_replacements,
    merge_segment_text,
    normalize_hotkey,
)


SAMPLE_RATE = 16_000


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
    finished_text = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        audio_path: Path,
        model_name: str,
        language: str,
        hf_endpoint: str,
        replacement_pairs: list[str],
        replacements_file: Path | None,
    ) -> None:
        super().__init__()
        self.audio_path = audio_path
        self.model_name = model_name
        self.language = language
        self.hf_endpoint = hf_endpoint
        self.replacement_pairs = replacement_pairs
        self.replacements_file = replacements_file

    def run(self) -> None:
        try:
            model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            segments, _info = model.transcribe(str(self.audio_path), language=self.language)
            replacements = load_replacements(self.replacements_file, self.replacement_pairs)
            texts = [apply_replacements(segment.text.strip(), replacements) for segment in segments if segment.text.strip()]
            self.finished_text.emit(merge_segment_text(texts))
        except BaseException as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class VoiceDesktop(QWidget):
    hotkey_pressed = Signal()

    def __init__(
        self,
        *,
        hotkey: str,
        language: str,
        model_name: str,
        paste_to_active_app: bool,
        submit_to_active_app: bool,
        hf_endpoint: str,
        replacement_pairs: list[str],
        replacements_file: Path | None,
    ) -> None:
        super().__init__()
        self.hotkey = hotkey
        self.language = language
        self.model_name = model_name
        self.paste_to_active_app = paste_to_active_app
        self.submit_to_active_app = submit_to_active_app
        self.hf_endpoint = hf_endpoint
        self.replacement_pairs = replacement_pairs
        self.replacements_file = replacements_file
        self.recorder = AudioRecorder()
        self.worker: TranscribeWorker | None = None
        self.hotkey_listener: keyboard.GlobalHotKeys | None = None

        self.setWindowTitle("Copilot Voice Sprite")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setMinimumWidth(360)

        self.orb = QLabel("•ᴗ•")
        self.orb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.orb.setFixedSize(132, 132)
        self.orb.setFont(QFont("Arial", 32, QFont.Weight.Bold))

        self.status = QLabel("IDLE")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setFont(QFont("Arial", 16, QFont.Weight.Bold))

        self.tip = QLabel(f"Hotkey: {hotkey}")
        self.tip.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.transcript = QTextEdit()
        self.transcript.setPlaceholderText("Transcript will appear here...")
        self.transcript.setMinimumHeight(90)

        self.error = QLabel("Ready.")
        self.error.setWordWrap(True)

        self.start_button = QPushButton("Start Recording")
        self.stop_button = QPushButton("Stop Recording")
        self.quit_button = QPushButton("Quit")

        buttons = QHBoxLayout()
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        buttons.addWidget(self.quit_button)

        layout = QVBoxLayout()
        layout.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)
        layout.addWidget(self.tip)
        layout.addLayout(buttons)
        layout.addWidget(self.transcript)
        layout.addWidget(self.error)
        self.setLayout(layout)

        self.start_button.clicked.connect(self.start_recording)
        self.stop_button.clicked.connect(self.stop_recording)
        self.quit_button.clicked.connect(self.close)
        self.hotkey_pressed.connect(self.toggle_recording)
        self._set_stage("idle")

    def start_hotkey(self) -> None:
        self.hotkey_listener = keyboard.GlobalHotKeys({normalize_hotkey(self.hotkey): self.hotkey_pressed.emit})
        self.hotkey_listener.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        event.accept()

    def toggle_recording(self) -> None:
        if self.recorder.is_recording():
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        try:
            self.recorder.start()
            self._set_stage("recording")
            self.error.setText("Recording...")
        except BaseException as exc:  # noqa: BLE001
            self._set_stage("error")
            self.error.setText(f"Start failed: {exc}")

    def stop_recording(self) -> None:
        try:
            audio_path = self.recorder.stop()
            self._set_stage("transcribing")
            self.error.setText(f"Transcribing {audio_path.name}...")
            self.worker = TranscribeWorker(
                audio_path,
                self.model_name,
                self.language,
                self.hf_endpoint,
                self.replacement_pairs,
                self.replacements_file,
            )
            self.worker.finished_text.connect(self._on_transcribed)
            self.worker.failed.connect(self._on_failed)
            self.worker.start()
        except BaseException as exc:  # noqa: BLE001
            self._set_stage("error")
            self.error.setText(f"Stop failed: {exc}")

    def _on_transcribed(self, text: str) -> None:
        self.transcript.setPlainText(text)
        self._set_stage("done")
        self.error.setText("Done.")
        if self.paste_to_active_app or self.submit_to_active_app:
            self._paste_text(text)

    def _on_failed(self, message: str) -> None:
        self._set_stage("error")
        self.error.setText(message)

    def _paste_text(self, text: str) -> None:
        pyperclip.copy(text)
        controller = keyboard.Controller()
        modifier = keyboard.Key.cmd if platform.system() == "Darwin" else keyboard.Key.ctrl
        self.hide()
        time.sleep(0.15)
        with controller.pressed(modifier):
            controller.press("v")
            controller.release("v")
        if self.submit_to_active_app:
            time.sleep(0.1)
            controller.press(keyboard.Key.enter)
            controller.release(keyboard.Key.enter)
        self.show()

    def _set_stage(self, stage: str) -> None:
        colors = {
            "idle": "#7DB7FF",
            "recording": "#FF5D73",
            "transcribing": "#FFD166",
            "done": "#57CC99",
            "error": "#FF6B6B",
        }
        faces = {
            "idle": "•ᴗ•",
            "recording": "●ᴗ●",
            "transcribing": "•…•",
            "done": "•‿•",
            "error": "•︵•",
        }
        self.status.setText(stage.upper())
        self.orb.setText(faces.get(stage, "•ᴗ•"))
        self.orb.setStyleSheet(
            "border-radius: 66px;"
            f"background-color: {colors.get(stage, '#7DB7FF')};"
            "color: #09111f;"
        )


def run_qt_overlay(
    *,
    hotkey: str,
    language: str,
    model_name: str,
    paste_to_active_app: bool,
    submit_to_active_app: bool,
    hf_endpoint: str = DEFAULT_HF_ENDPOINT,
    replacement_pairs: list[str] | None = None,
    replacements_file: Path | None = None,
) -> None:
    app = QApplication.instance() or QApplication([])
    widget = VoiceDesktop(
        hotkey=hotkey,
        language=language,
        model_name=model_name,
        paste_to_active_app=paste_to_active_app,
        submit_to_active_app=submit_to_active_app,
        hf_endpoint=hf_endpoint,
        replacement_pairs=replacement_pairs or [],
        replacements_file=replacements_file,
    )
    widget.show()
    widget.start_hotkey()
    app.exec()
