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
from PySide6.QtCore import QTimer
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
    finished_text = Signal(str)
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
                self.finished_text.emit(
                    polish_text(
                        str(result["plain_text"]),
                        self.polish,
                        self.context_file,
                        language_preference=self.language_preference,
                        engine=self.polish_engine,
                        ollama_model=self.ollama_model,
                    )
                )
            else:
                model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                segments, _info = model.transcribe(str(self.audio_path), language=self.language)
                replacements = load_replacements(self.replacements_file, self.replacement_pairs)
                texts = [apply_replacements(segment.text.strip(), replacements) for segment in segments if segment.text.strip()]
                self.finished_text.emit(
                    polish_text(
                        merge_segment_text(texts),
                        self.polish,
                        self.context_file,
                        language_preference=self.language_preference,
                        engine=self.polish_engine,
                        ollama_model=self.ollama_model,
                    )
                )
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
        backend: str,
        mlx_model: str,
        paste_to_active_app: bool,
        submit_to_active_app: bool,
        hf_endpoint: str,
        replacement_pairs: list[str],
        replacements_file: Path | None,
        polish: str,
        context_file: Path | None,
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
        self.language_preference = language_preference
        self.polish_engine = polish_engine
        self.ollama_model = ollama_model
        self.recorder = AudioRecorder()
        self.worker: TranscribeWorker | None = None
        self.hotkey_listener: keyboard.GlobalHotKeys | None = None
        self._topmost_timer: QTimer | None = None
        self._focus_timer: QTimer | None = None
        self._preferred_target: FocusTarget | None = None
        self._recording_target: FocusTarget | None = None

        self.setWindowTitle("Copilot Voice Sprite")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMinimumWidth(360)
        self.resize(420, 420)
        self.move(80, 80)

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
        self._install_topmost_guard()

    def start_hotkey(self) -> None:
        self.hotkey_listener = keyboard.GlobalHotKeys({normalize_hotkey(self.hotkey): self.hotkey_pressed.emit})
        self.hotkey_listener.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        if self._topmost_timer is not None:
            self._topmost_timer.stop()
        if self._focus_timer is not None:
            self._focus_timer.stop()
        event.accept()

    def toggle_recording(self) -> None:
        if self.recorder.is_recording():
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        try:
            self._recording_target = self._preferred_target or self._current_focus_target()
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
                self.backend,
                self.mlx_model,
                self.language,
                self.hf_endpoint,
                self.replacement_pairs,
                self.replacements_file,
                self.polish,
                self.context_file,
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
        self._restore_focus_target(self._recording_target or self._preferred_target)
        time.sleep(0.25)
        with controller.pressed(modifier):
            controller.press("v")
            controller.release("v")
        if self.submit_to_active_app:
            time.sleep(0.1)
            controller.press(keyboard.Key.enter)
            controller.release(keyboard.Key.enter)
        self.show()
        self.enforce_topmost()

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
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
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
        language_preference=language_preference,
        polish_engine=polish_engine,
        ollama_model=ollama_model,
    )
    widget.show()
    widget.raise_()
    widget.enforce_topmost()
    widget.start_hotkey()
    print("Qt desktop overlay shown. Press the configured hotkey or use the buttons.", flush=True)
    app.exec()
