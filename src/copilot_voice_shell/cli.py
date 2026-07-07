from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import time
import subprocess
import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .polish import polish_text
from .session_context import find_active_copilot_session_id
from . import config as _config

DEFAULT_LANGUAGE = "zh"
DEFAULT_MODEL = "small"
DEFAULT_BACKEND = "faster-whisper"
DEFAULT_MLX_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"
DEFAULT_HOTKEY = "cmd+shift+space"
DEFAULT_POLISH = "off"
DEFAULT_POLISH_ENGINE = "rules"
DEFAULT_OLLAMA_MODEL = "qwen3:latest"
DEFAULT_LANGUAGE_PREFERENCE = "zh-en"
SILENT_PEAK_THRESHOLD = 1e-6


def apply_config_defaults(cfg: dict[str, Any]) -> None:
    """Override module-level argparse defaults from the loaded config file."""
    global DEFAULT_LANGUAGE, DEFAULT_MODEL, DEFAULT_BACKEND, DEFAULT_MLX_MODEL
    global DEFAULT_HF_ENDPOINT, DEFAULT_HOTKEY, DEFAULT_POLISH, DEFAULT_POLISH_ENGINE
    global DEFAULT_OLLAMA_MODEL, DEFAULT_LANGUAGE_PREFERENCE
    DEFAULT_LANGUAGE = cfg.get("language", DEFAULT_LANGUAGE)
    DEFAULT_MODEL = cfg.get("model", DEFAULT_MODEL)
    DEFAULT_BACKEND = cfg.get("backend", DEFAULT_BACKEND)
    DEFAULT_MLX_MODEL = cfg.get("mlx_model", DEFAULT_MLX_MODEL)
    DEFAULT_HF_ENDPOINT = cfg.get("hf_endpoint", DEFAULT_HF_ENDPOINT)
    DEFAULT_HOTKEY = cfg.get("hotkey", DEFAULT_HOTKEY)
    DEFAULT_POLISH = cfg.get("polish", DEFAULT_POLISH)
    DEFAULT_POLISH_ENGINE = cfg.get("polish_engine", DEFAULT_POLISH_ENGINE)
    DEFAULT_OLLAMA_MODEL = cfg.get("ollama_model", DEFAULT_OLLAMA_MODEL)
    DEFAULT_LANGUAGE_PREFERENCE = cfg.get("language_preference", DEFAULT_LANGUAGE_PREFERENCE)


@dataclass(frozen=True)
class SegmentLine:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class RecognitionInfo:
    language: str
    language_probability: float


@dataclass(frozen=True)
class AppTarget:
    name: str
    bundle_id: str
    pid: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="copilot-voice-shell",
        description="Local voice shell for Copilot: record audio and transcribe it with faster-whisper.",
    )
    subparsers = parser.add_subparsers(dest="command")

    capture_parser = subparsers.add_parser(
        "capture",
        help="Record from the default microphone, then transcribe and print the result.",
    )
    add_common_options(capture_parser)
    capture_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Audio output path. Defaults to ./recordings/<timestamp>.m4a",
    )

    record_parser = subparsers.add_parser(
        "record",
        help="Only record audio from the default microphone.",
    )
    record_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Audio output path. Defaults to ./recordings/<timestamp>.m4a",
    )

    transcribe_parser = subparsers.add_parser(
        "transcribe",
        help="Transcribe an existing audio file.",
    )
    add_common_options(transcribe_parser)
    transcribe_parser.add_argument("audio", type=Path, help="Path to the audio file.")

    hotkey_parser = subparsers.add_parser(
        "hotkey",
        help="Run a global push-to-talk style listener using a system-wide hotkey.",
    )
    add_common_options(hotkey_parser)
    hotkey_parser.add_argument(
        "--hotkey",
        default=DEFAULT_HOTKEY,
        help="Global hotkey, e.g. cmd+shift+space or ctrl+alt+r.",
    )

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="Start a local status dashboard with the sprite UI and background hotkey listener.",
    )
    add_common_options(dashboard_parser)
    dashboard_parser.add_argument("--hotkey", default=DEFAULT_HOTKEY, help="Global hotkey for recording.")
    dashboard_parser.add_argument("--host", default="127.0.0.1", help="Dashboard bind host.")
    dashboard_parser.add_argument("--port", type=int, default=8765, help="Dashboard bind port.")
    dashboard_parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not auto-open the dashboard in a browser.",
    )

    overlay_parser = subparsers.add_parser(
        "overlay",
        help="Show an always-on-top sprite overlay while the background hotkey listener runs.",
    )
    add_common_options(overlay_parser)
    overlay_parser.add_argument("--hotkey", default=DEFAULT_HOTKEY, help="Global hotkey for recording.")

    desktop_parser = subparsers.add_parser(
        "desktop",
        help="Start the cross-platform Qt desktop overlay for macOS and Windows.",
    )
    add_common_options(desktop_parser)
    desktop_parser.add_argument("--hotkey", default=DEFAULT_HOTKEY, help="Global hotkey for recording.")

    send_parser = subparsers.add_parser(
        "send",
        help="Send text into the active macOS app, e.g. a Copilot CLI session.",
    )
    send_parser.add_argument("text", nargs="?", help="Text to send. If omitted, reads from stdin.")
    send_parser.add_argument(
        "--from-file",
        type=Path,
        default=None,
        help="Read text from a file instead of an argument or stdin.",
    )
    send_parser.add_argument(
        "--submit",
        action="store_true",
        help="Press Return after pasting, useful for sending the prompt immediately.",
    )

    download_parser = subparsers.add_parser(
        "download-model",
        help="Pre-download a Whisper model so capture does not block on first use.",
    )
    download_parser.add_argument("--model", default=DEFAULT_MODEL, help="Whisper model name.")
    download_parser.add_argument(
        "--hf-endpoint",
        default=os.environ.get("HF_ENDPOINT", DEFAULT_HF_ENDPOINT),
        help="Model download endpoint for Hugging Face-compatible downloads.",
    )

    subparsers.add_parser(
        "doctor",
        help="Check local prerequisites for recording and transcription.",
    )

    return parser


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="Language hint, e.g. zh or en.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Whisper model name, e.g. small or medium.")
    parser.add_argument(
        "--backend",
        choices=["faster-whisper", "mlx", "azure"],
        default=DEFAULT_BACKEND,
        help="Transcription backend. 'mlx' for Apple Silicon GPU; 'azure' for Azure OpenAI.",
    )
    parser.add_argument(
        "--mlx-model",
        default=DEFAULT_MLX_MODEL,
        help="MLX Whisper model repo/path when --backend=mlx.",
    )
    parser.add_argument(
        "--copy",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Copy the plain transcription text to the clipboard. For the desktop "
        "overlay, leaving this unset uses the saved setting.",
    )
    parser.add_argument(
        "--paste",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Paste the plain transcription into the active app after copying it. For "
        "the desktop overlay, leaving this unset uses the saved setting.",
    )
    parser.add_argument(
        "--submit",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Press Return after pasting. Implies --paste.",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Print only the merged transcription text.",
    )
    parser.add_argument(
        "--save-text",
        type=Path,
        default=None,
        help="Optional path for saving the plain transcription text.",
    )
    parser.add_argument(
        "--replace",
        action="append",
        default=[],
        metavar="FROM=TO",
        help="Inline phrase replacement. Can be passed multiple times.",
    )
    parser.add_argument(
        "--replacements-file",
        type=Path,
        default=None,
        help="JSON file containing phrase replacements, e.g. {\"Scale\": \"skill\"}.",
    )
    parser.add_argument(
        "--hf-endpoint",
        default=os.environ.get("HF_ENDPOINT", DEFAULT_HF_ENDPOINT),
        help="Model download endpoint for Hugging Face-compatible downloads.",
    )
    parser.add_argument(
        "--streaming",
        action="store_true",
        help="Enable experimental streaming transcription during recording.",
    )
    parser.add_argument(
        "--polish",
        choices=["off", "copilot", "auto", "dev", "im", "notes", "email", "browser"],
        default=DEFAULT_POLISH,
        help="Post-process dictated text. 'auto' detects active app and selects style; others force specific styles.",
    )
    parser.add_argument(
        "--polish-engine",
        choices=["rules", "ollama", "azure"],
        default=DEFAULT_POLISH_ENGINE,
        help="Polish implementation. rules is deterministic; ollama uses a local model; azure uses Azure OpenAI chat.",
    )
    parser.add_argument(
        "--ollama-model",
        default=DEFAULT_OLLAMA_MODEL,
        help="Local Ollama model used when --polish-engine=ollama.",
    )
    parser.add_argument(
        "--context-file",
        type=Path,
        default=None,
        help="Optional session summary/context file used when --polish=copilot.",
    )
    parser.add_argument(
        "--session-context",
        action="store_true",
        help="Use recent active Copilot CLI session context for local polish.",
    )
    parser.add_argument(
        "--language-preference",
        default=DEFAULT_LANGUAGE_PREFERENCE,
        choices=["zh-en", "en", "auto"],
        help="Preferred dictation language mix used for cleanup. zh-en filters common wrong-script hallucinations.",
    )


def main(argv: Sequence[str] | None = None) -> None:
    for _stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(_stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    apply_config_defaults(_config.load_config())
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command or "capture"
    if command == "capture":
        run_capture(
            output=args.output,
            language=args.language,
            model_name=args.model,
            backend=args.backend,
            mlx_model=args.mlx_model,
            copy_to_clipboard=args.copy,
            paste_to_active_app=args.paste,
            submit_to_active_app=args.submit,
            plain=args.plain,
            save_text=args.save_text,
            hf_endpoint=args.hf_endpoint,
            replacement_pairs=args.replace,
            replacements_file=args.replacements_file,
            streaming=args.streaming,
            polish=args.polish,
            context_file=args.context_file,
            session_context=args.session_context,
            language_preference=args.language_preference,
            polish_engine=args.polish_engine,
            ollama_model=args.ollama_model,
        )
        return
    if command == "record":
        path = record_audio(args.output)
        print(path)
        return
    if command == "transcribe":
        result = transcribe_audio(
            args.audio,
            args.language,
            args.model,
            args.backend,
            args.mlx_model,
            args.hf_endpoint,
            replacement_pairs=args.replace,
            replacements_file=args.replacements_file,
        )
        emit_transcription(
            apply_polish_to_result(
                result,
                args.polish,
                args.context_file,
                args.session_context,
                args.language_preference,
                args.polish_engine,
                args.ollama_model,
            ),
            plain=args.plain,
            copy_to_clipboard=args.copy,
            paste_to_active_app=args.paste,
            submit_to_active_app=args.submit,
            save_text=args.save_text,
        )
        return
    if command == "hotkey":
        run_hotkey_mode(
            hotkey=args.hotkey,
            language=args.language,
            model_name=args.model,
            backend=args.backend,
            mlx_model=args.mlx_model,
            copy_to_clipboard=args.copy,
            paste_to_active_app=args.paste,
            submit_to_active_app=args.submit,
            plain=args.plain,
            save_text=args.save_text,
            hf_endpoint=args.hf_endpoint,
            replacement_pairs=args.replace,
            replacements_file=args.replacements_file,
            streaming=args.streaming,
            polish=args.polish,
            context_file=args.context_file,
            session_context=args.session_context,
            language_preference=args.language_preference,
            polish_engine=args.polish_engine,
            ollama_model=args.ollama_model,
        )
        return
    if command == "dashboard":
        from .dashboard import run_dashboard_server

        run_dashboard_server(
            host=args.host,
            port=args.port,
            open_browser=not args.no_open_browser,
            hotkey=args.hotkey,
            language=args.language,
            model_name=args.model,
            backend=args.backend,
            mlx_model=args.mlx_model,
            copy_to_clipboard=args.copy,
            paste_to_active_app=args.paste,
            submit_to_active_app=args.submit,
            plain=args.plain,
            save_text=args.save_text,
            hf_endpoint=args.hf_endpoint,
            replacement_pairs=args.replace,
            replacements_file=args.replacements_file,
            streaming=args.streaming,
            polish=args.polish,
            context_file=args.context_file,
            session_context=args.session_context,
            language_preference=args.language_preference,
            polish_engine=args.polish_engine,
            ollama_model=args.ollama_model,
        )
        return
    if command == "overlay":
        from .overlay import run_overlay

        run_overlay(
            hotkey=args.hotkey,
            language=args.language,
            model_name=args.model,
            backend=args.backend,
            mlx_model=args.mlx_model,
            copy_to_clipboard=args.copy,
            paste_to_active_app=args.paste,
            submit_to_active_app=args.submit,
            plain=args.plain,
            save_text=args.save_text,
            hf_endpoint=args.hf_endpoint,
            replacement_pairs=args.replace,
            replacements_file=args.replacements_file,
            streaming=args.streaming,
            polish=args.polish,
            context_file=args.context_file,
            session_context=args.session_context,
            language_preference=args.language_preference,
            polish_engine=args.polish_engine,
            ollama_model=args.ollama_model,
        )
        return
    if command == "desktop":
        if sys.platform == "darwin":
            from .overlay import run_overlay

            run_overlay(
                hotkey=args.hotkey,
                language=args.language,
                model_name=args.model,
                backend=args.backend,
                mlx_model=args.mlx_model,
                copy_to_clipboard=args.copy,
                paste_to_active_app=args.paste,
                submit_to_active_app=args.submit,
                plain=args.plain,
                save_text=args.save_text,
                hf_endpoint=args.hf_endpoint,
                replacement_pairs=args.replace,
                replacements_file=args.replacements_file,
                streaming=args.streaming,
                polish=args.polish,
                context_file=args.context_file,
                session_context=args.session_context,
                language_preference=args.language_preference,
                polish_engine=args.polish_engine,
                ollama_model=args.ollama_model,
            )
        else:
            from .qt_overlay import run_qt_overlay

            run_qt_overlay(
                hotkey=args.hotkey,
                language=args.language,
                model_name=args.model,
                backend=args.backend,
                mlx_model=args.mlx_model,
                paste_to_active_app=args.paste,
                submit_to_active_app=args.submit,
                copy_to_clipboard=args.copy,
                hf_endpoint=args.hf_endpoint,
                replacement_pairs=args.replace,
                replacements_file=args.replacements_file,
                polish=args.polish,
                context_file=args.context_file,
                session_context=args.session_context,
                language_preference=args.language_preference,
                polish_engine=args.polish_engine,
                ollama_model=args.ollama_model,
            )
        return
    if command == "send":
        text = resolve_send_text(args.text, args.from_file)
        send_text_to_active_app(text, submit=args.submit)
        return
    if command == "download-model":
        model_path = predownload_model(args.model, args.hf_endpoint)
        print(model_path)
        return
    if command == "doctor":
        run_doctor()
        return

    parser.error(f"Unknown command: {command}")


def run_capture(
    *,
    output: Path | None,
    language: str,
    model_name: str,
    backend: str,
    mlx_model: str,
    copy_to_clipboard: bool,
    paste_to_active_app: bool,
    submit_to_active_app: bool,
    plain: bool,
    save_text: Path | None,
    hf_endpoint: str,
    replacement_pairs: Sequence[str],
    replacements_file: Path | None,
    streaming: bool,
    polish: str,
    context_file: Path | None,
    session_context: bool,
    language_preference: str,
    polish_engine: str,
    ollama_model: str,
) -> None:
    target_app = None
    try:
        target_app = get_frontmost_app_info()
    except BaseException:
        pass

    audio_path = record_audio(output)
    result = transcribe_audio(
        audio_path,
        language,
        model_name,
        backend,
        mlx_model,
        hf_endpoint,
        replacement_pairs=replacement_pairs,
        replacements_file=replacements_file,
    )
    emit_transcription(
        apply_polish_to_result(result, polish, context_file, session_context, language_preference, polish_engine, ollama_model, target_app=target_app),
        plain=plain,
        copy_to_clipboard=copy_to_clipboard,
        paste_to_active_app=paste_to_active_app,
        submit_to_active_app=submit_to_active_app,
        save_text=save_text,
    )


def run_hotkey_mode(
    *,
    hotkey: str,
    language: str,
    model_name: str,
    backend: str,
    mlx_model: str,
    copy_to_clipboard: bool,
    paste_to_active_app: bool,
    submit_to_active_app: bool,
    plain: bool,
    save_text: Path | None,
    hf_endpoint: str,
    replacement_pairs: Sequence[str],
    replacements_file: Path | None,
    streaming: bool,
    polish: str,
    context_file: Path | None,
    session_context: bool,
    language_preference: str,
    polish_engine: str,
    ollama_model: str,
) -> None:
    hotkey_spec = normalize_hotkey(hotkey)
    should_copy = copy_to_clipboard or not (paste_to_active_app or submit_to_active_app)
    should_paste = paste_to_active_app or submit_to_active_app

    session = HotkeySession(
        language=language,
        model_name=model_name,
        backend=backend,
        mlx_model=mlx_model,
        copy_to_clipboard=should_copy,
        paste_to_active_app=should_paste,
        submit_to_active_app=submit_to_active_app,
        plain=plain,
        save_text=save_text,
        hf_endpoint=hf_endpoint,
        replacement_pairs=replacement_pairs,
        replacements_file=replacements_file,
        streaming=streaming,
        polish=polish,
        context_file=context_file,
        session_context=session_context,
        language_preference=language_preference,
        polish_engine=polish_engine,
        ollama_model=ollama_model,
    )

    from pynput import keyboard

    print(f"Hotkey mode is running. Press {hotkey} to start/stop recording. Ctrl+C exits.")
    with keyboard.GlobalHotKeys({hotkey_spec: session.toggle_recording}) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            session.stop_if_recording()
            raise SystemExit(0)


def record_audio(output: Path | None) -> Path:
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    audio_path = output or default_recording_path()
    if audio_path.suffix.lower() not in (".wav", ".flac"):
        audio_path = audio_path.with_suffix(".wav")
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    sample_rate = 16_000
    chunks: list[Any] = []
    lock = threading.Lock()

    def _callback(indata: Any, _frames: int, _time_info: Any, _status: Any) -> None:
        with lock:
            chunks.append(indata.copy())

    print("Press Enter to start recording.")
    input()

    stream = sd.InputStream(
        samplerate=sample_rate,
        blocksize=sample_rate // 10,
        latency="high",
        channels=1,
        dtype="float32",
        callback=_callback,
    )
    stream.start()
    try:
        print("Recording... Press Enter to stop.")
        input()
    finally:
        stream.stop()
        stream.close()

    with lock:
        captured = list(chunks)
    if not captured:
        raise SystemExit("Recording failed: no audio samples captured.")
    audio = np.concatenate(captured, axis=0)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= SILENT_PEAK_THRESHOLD:
        raise SystemExit("Recording failed: captured only silence. Check microphone permission and input device.")
    sf.write(str(audio_path), audio, sample_rate)

    print(f"Saved audio to {audio_path}")
    return audio_path


def transcribe_audio(
    audio: Path,
    language: str,
    model_name: str,
    backend: str,
    mlx_model: str,
    hf_endpoint: str,
    *,
    replacement_pairs: Sequence[str],
    replacements_file: Path | None,
) -> dict[str, object]:
    if not audio.exists():
        raise SystemExit(f"Audio file not found: {audio}")

    os.environ["HF_ENDPOINT"] = hf_endpoint

    if backend == "mlx":
        return transcribe_audio_mlx(
            audio,
            language,
            mlx_model,
            replacement_pairs=replacement_pairs,
            replacements_file=replacements_file,
        )

    if backend == "azure":
        return transcribe_audio_azure(
            audio,
            language,
            replacement_pairs=replacement_pairs,
            replacements_file=replacements_file,
        )

    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(audio), language=language)
    replacement_map = load_replacements(replacements_file, replacement_pairs)

    segment_lines: list[SegmentLine] = []
    raw_lines: list[str] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        raw_lines.append(text)
        segment_lines.append(
            SegmentLine(
                start=segment.start,
                end=segment.end,
                text=apply_replacements(text, replacement_map),
            )
        )

    plain_text = merge_segment_text(line.text for line in segment_lines)
    return {
        "info": info,
        "segments": segment_lines,
        "plain_text": plain_text,
        "raw_text": merge_segment_text(raw_lines),
        "replacement_map": replacement_map,
    }


def transcribe_audio_mlx(
    audio: Path,
    language: str,
    mlx_model: str,
    *,
    replacement_pairs: Sequence[str],
    replacements_file: Path | None,
) -> dict[str, object]:
    if not audio.exists():
        raise SystemExit(f"Audio file not found: {audio}")

    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(audio),
        path_or_hf_repo=mlx_model,
        language=language,
        verbose=False,
    )
    replacement_map = load_replacements(replacements_file, replacement_pairs)

    segment_lines: list[SegmentLine] = []
    raw_lines: list[str] = []
    for segment in result.get("segments", []):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        raw_lines.append(text)
        segment_lines.append(
            SegmentLine(
                start=float(segment.get("start", 0.0)),
                end=float(segment.get("end", 0.0)),
                text=apply_replacements(text, replacement_map),
            )
        )

    if not segment_lines:
        text = str(result.get("text", "")).strip()
        if text:
            raw_lines.append(text)
            segment_lines.append(SegmentLine(0.0, 0.0, apply_replacements(text, replacement_map)))

    plain_text = merge_segment_text(line.text for line in segment_lines)
    return {
        "info": RecognitionInfo(language=language, language_probability=1.0),
        "segments": segment_lines,
        "plain_text": plain_text,
        "raw_text": merge_segment_text(raw_lines),
        "replacement_map": replacement_map,
    }


def build_azure_prompt(replacement_map: dict[str, str]) -> str:
    from .polish import GLOSSARY

    terms: list[str] = list(GLOSSARY)
    for source, target in replacement_map.items():
        terms.append(source)
        terms.append(target)
    seen: list[str] = []
    for term in terms:
        term = term.strip()
        if term and term not in seen:
            seen.append(term)
    return "Domain terms: " + ", ".join(seen) if seen else ""


def transcribe_audio_azure(
    audio: Path,
    language: str,
    *,
    replacement_pairs: Sequence[str],
    replacements_file: Path | None,
    language_preference: str | None = None,
) -> dict[str, object]:
    if not audio.exists():
        raise SystemExit(f"Audio file not found: {audio}")

    from . import azure_client

    preference = language_preference or DEFAULT_LANGUAGE_PREFERENCE
    lang_hint = azure_client.transcribe_language_hint(preference)

    replacement_map = load_replacements(replacements_file, replacement_pairs)
    prompt = build_azure_prompt(replacement_map)
    text = azure_client.transcribe(audio, language=lang_hint, prompt=prompt)

    segment_lines: list[SegmentLine] = []
    raw_lines: list[str] = []
    if text:
        raw_lines.append(text)
        segment_lines.append(SegmentLine(0.0, 0.0, apply_replacements(text, replacement_map)))

    plain_text = merge_segment_text(line.text for line in segment_lines)
    return {
        "info": RecognitionInfo(language=lang_hint or "auto", language_probability=1.0),
        "segments": segment_lines,
        "plain_text": plain_text,
        "raw_text": merge_segment_text(raw_lines),
        "replacement_map": replacement_map,
    }


def emit_transcription(
    result: dict[str, object],
    *,
    plain: bool,
    copy_to_clipboard: bool,
    paste_to_active_app: bool,
    submit_to_active_app: bool,
    save_text: Path | None,
    target_app: AppTarget | None = None,
) -> dict[str, object]:
    plain_text = result["plain_text"]
    assert isinstance(plain_text, str)
    should_paste = paste_to_active_app or submit_to_active_app
    outcome: dict[str, object] = {
        "copied": False,
        "pasted": False,
        "submitted": False,
        "target_app": None,
    }

    if plain:
        print(plain_text)
    else:
        print(format_verbose_output(result))

    if save_text is not None:
        save_text.parent.mkdir(parents=True, exist_ok=True)
        save_text.write_text(plain_text + "\n", encoding="utf-8")
        print(f"\nSaved text to {save_text}")

    if copy_to_clipboard or should_paste:
        copy_text(plain_text)
        print("\nCopied plain transcription to clipboard.")
        outcome["copied"] = True
    if should_paste:
        paste_target = target_app or get_frontmost_app_info()
        print(f"Attempting paste into frontmost app: {paste_target.name}")
        paste_from_clipboard(submit=submit_to_active_app, target_app=paste_target)
        print(
            f"Paste keystroke sent to: {paste_target.name}"
            + (" (with submit)" if submit_to_active_app else "")
        )
        outcome["pasted"] = True
        outcome["submitted"] = submit_to_active_app
        outcome["target_app"] = paste_target.name
    return outcome


def apply_polish_to_result(
    result: dict[str, object],
    mode: str,
    context_file: Path | None,
    session_context: bool,
    language_preference: str,
    engine: str,
    ollama_model: str,
    target_app: AppTarget | None = None,
) -> dict[str, object]:
    plain_text = result.get("plain_text")
    if not isinstance(plain_text, str):
        return result
    polished = polish_text(
        plain_text,
        mode,
        context_file,
        session_context=session_context,
        language_preference=language_preference,
        engine=engine,
        ollama_model=ollama_model,
        target_app_name=target_app.name if target_app else None,
        target_app_bundle_id=target_app.bundle_id if target_app else None,
    )
    updated = dict(result)
    updated["plain_text"] = polished
    updated["raw_text"] = result.get("raw_text") or plain_text
    return updated


def format_verbose_output(result: dict[str, object]) -> str:
    info = result["info"]
    segments = result["segments"]
    raw_text = result["raw_text"]
    plain_text = result["plain_text"]
    replacement_map = result["replacement_map"]

    lines = [f"Detected language: {info.language} (prob={info.language_probability:.3f})"]
    assert isinstance(segments, list)
    for segment in segments:
        assert isinstance(segment, SegmentLine)
        lines.append(f"[{segment.start:.2f}-{segment.end:.2f}] {segment.text}")

    if replacement_map:
        assert isinstance(raw_text, str)
        assert isinstance(plain_text, str)
        lines.append("")
        lines.append(f"Raw text: {raw_text}")
        lines.append(f"Plain text: {plain_text}")

    return "\n".join(lines)


def predownload_model(model_name: str, hf_endpoint: str) -> str:
    from faster_whisper.utils import download_model
    os.environ["HF_ENDPOINT"] = hf_endpoint
    return download_model(model_name)


class HotkeySession:
    def __init__(
        self,
        *,
        language: str,
        model_name: str,
        backend: str,
        mlx_model: str,
        copy_to_clipboard: bool,
        paste_to_active_app: bool,
        submit_to_active_app: bool,
        plain: bool,
        save_text: Path | None,
        hf_endpoint: str,
        replacement_pairs: Sequence[str],
        replacements_file: Path | None,
        status_reporter: Callable[[dict[str, object]], None] | None = None,
        streaming: bool = False,
        polish: str = "off",
        context_file: Path | None = None,
        session_context: bool = False,
        language_preference: str = "zh-en",
        polish_engine: str = "rules",
        ollama_model: str = "qwen3:latest",
    ) -> None:
        self.language = language
        self.model_name = model_name
        self.backend = backend
        self.mlx_model = mlx_model
        self.copy_to_clipboard = copy_to_clipboard
        self.paste_to_active_app = paste_to_active_app
        self.submit_to_active_app = submit_to_active_app
        self.plain = plain
        self.save_text = save_text
        self.hf_endpoint = hf_endpoint
        self.replacement_pairs = tuple(replacement_pairs)
        self.replacements_file = replacements_file
        self.status_reporter = status_reporter
        self.streaming = streaming
        self.polish = polish
        self.context_file = context_file
        self.session_context = session_context
        self.language_preference = language_preference
        self.polish_engine = polish_engine
        self.ollama_model = ollama_model
        self._lock = threading.Lock()
        self._recording_process: subprocess.Popen[bytes] | None = None
        self._current_audio_path: Path | None = None
        self.target_app_getter: Callable[[], AppTarget | None] | None = None
        self._target_app: AppTarget | None = None
        self._recording_stderr_path: Path | None = None
        self._recording_dir: Path | None = None
        self._chunk_dir: Path | None = None
        self._stream_stop = threading.Event()
        self._stream_worker: threading.Thread | None = None
        self._streamed_segments: list[SegmentLine] = []
        self._processed_chunks: set[Path] = set()
        self._model_lock = threading.Lock()
        self._model: WhisperModel | None = None
        self._audio_stream: Any | None = None
        self._audio_chunks: list[Any] = []
        self._audio_lock = threading.Lock()
        self._sd_buffer_mode = False
        self._input_device_label = ""
        self._session_context_id = find_active_copilot_session_id() if session_context else ""
        if self._session_context_id:
            self._report_status({"error": f"Copilot session: {self._session_context_id[:8]}..."})

    def toggle_recording(self) -> None:
        try:
            with self._lock:
                if not self._is_recording():
                    self._start_recording()
                else:
                    self._stop_and_process_recording()
        except BaseException as exc:  # noqa: BLE001
            self._recording_process = None
            self._report_status({"stage": "error", "error": str(exc)})
            print(f"[hotkey] ERROR ({type(exc).__name__}): {exc}", file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)

    def start_recording(self) -> None:
        with self._lock:
            if self._is_recording():
                raise RuntimeError("Recording is already in progress.")
            self._start_recording()

    def stop_recording(self) -> None:
        with self._lock:
            if not self._is_recording():
                raise RuntimeError("Recording is not in progress.")
            self._stop_and_process_recording()

    def stop_if_recording(self) -> None:
        with self._lock:
            if not self._is_recording():
                return
            self._stop_streaming_audio()

    def _start_recording(self) -> None:
        self._current_audio_path = default_hotkey_recording_path().with_suffix(".wav")
        self._current_audio_path.parent.mkdir(parents=True, exist_ok=True)
        self._recording_stderr_path = self._current_audio_path.with_suffix(".log")
        self._target_app = self.target_app_getter() if self.target_app_getter is not None else get_frontmost_app_info()
        self._stream_stop = threading.Event()
        self._streamed_segments = []
        self._processed_chunks = set()
        if self.session_context:
            self._session_context_id = find_active_copilot_session_id()
        if self.streaming:
            self._recording_dir = self._current_audio_path.with_suffix("")
            self._recording_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._recording_dir = None
        self._chunk_dir = None
        self._prepare_backend()
        self._start_streaming_audio()
        if self.streaming:
            self._stream_worker = threading.Thread(target=self._stream_transcribe_loop, daemon=True)
            self._stream_worker.start()
        else:
            self._stream_worker = None
        self._report_status(
            {
                "stage": "recording",
                "audio_path": str(self._current_audio_path),
                "error": self._session_context_status(),
            }
        )
        print(f"[hotkey] Recording started: {self._current_audio_path}")

    def _stop_and_process_recording(self) -> None:
        assert self._current_audio_path is not None
        try:
            self._stop_streaming_audio()
        finally:
            if self.streaming:
                self._stream_stop.set()
                if self._stream_worker is not None:
                    self._stream_worker.join(timeout=5)
        if not self._current_audio_path.exists() or self._current_audio_path.stat().st_size == 0:
            raise RuntimeError("Recording failed: sounddevice did not produce an audio file.")
        print(f"[hotkey] Recording stopped. Transcribing {self._current_audio_path}...")
        self._report_status(
            {
                "stage": "transcribing",
                "audio_path": str(self._current_audio_path),
                "error": self._session_context_status(),
            }
        )
        raw_result = self._transcribe_with_loaded_model(self._current_audio_path)
        raw_text = raw_result["plain_text"]
        assert isinstance(raw_text, str)
        result = apply_polish_to_result(
            raw_result,
            self.polish,
            self.context_file,
            self.session_context,
            self.language_preference,
            self.polish_engine,
            self.ollama_model,
            target_app=self._target_app,
        )
        plain_text = result["plain_text"]
        assert isinstance(plain_text, str)
        print(f"[hotkey] Plain text length: {len(plain_text)}")
        self._report_status(
            {
                "stage": "transcribed",
                "audio_path": str(self._current_audio_path),
                "plain_text": raw_text,
                "raw_text": raw_text,
                "rephrased_text": plain_text,
                "error": self._session_context_status(),
            }
        )
        outcome = emit_transcription(
            result,
            plain=self.plain,
            copy_to_clipboard=self.copy_to_clipboard,
            paste_to_active_app=self.paste_to_active_app,
            submit_to_active_app=self.submit_to_active_app,
            save_text=self.save_text,
            target_app=self._target_app,
        )
        self._report_status(
            {
                "stage": "done",
                "audio_path": str(self._current_audio_path),
                "plain_text": raw_text,
                "raw_text": raw_text,
                "rephrased_text": plain_text,
                "copied": bool(outcome["copied"]),
                "pasted": bool(outcome["pasted"]),
                "submitted": bool(outcome["submitted"]),
                "target_app": outcome["target_app"] or "",
                "error": self._session_context_status(),
            }
        )

    def _stop_recording_process(self) -> None:
        assert self._recording_process is not None
        if self._recording_process.poll() is None:
            self._recording_process.send_signal(signal.SIGINT)
        self._recording_process.wait()
        self._recording_process = None

    def _read_recording_stderr(self) -> str:
        if self._recording_stderr_path is None or not self._recording_stderr_path.exists():
            return ""
        return self._recording_stderr_path.read_text(encoding="utf-8", errors="replace").strip()

    def _is_recording(self) -> bool:
        return self._recording_process is not None or self._audio_stream is not None

    def _ensure_model_loaded(self) -> WhisperModel:
        with self._model_lock:
            if self._model is None:
                from faster_whisper import WhisperModel
                self._report_status({"stage": "loading_model", "error": ""})
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
            return self._model

    def _prepare_backend(self) -> None:
        if self.backend == "mlx":
            self._report_status({"stage": "loading_model", "error": "Using MLX Apple Silicon backend."})
            import mlx.core as mx

            _ = mx.default_device()
            return
        if self.backend == "azure":
            self._report_status({"stage": "loading_model", "error": "Using Azure OpenAI backend."})
            return
        self._ensure_model_loaded()

    def _start_streaming_audio(self) -> None:
        import sounddevice as sd

        self._audio_chunks = []
        device_id, device_name = self._select_streaming_input_device(sd)
        self._input_device_label = f"{device_name} (#{device_id})"
        self._report_status({"stage": "recording", "error": f"Input device: {device_name} (#{device_id})"})
        self._audio_stream = sd.InputStream(
            device=device_id,
            samplerate=16_000,
            blocksize=1_600,
            latency="high",
            channels=1,
            dtype="float32",
            callback=self._on_stream_audio,
        )
        self._audio_stream.start()

    def _select_streaming_input_device(self, sd: Any) -> tuple[int, str]:
        devices = sd.query_devices()
        configured = str(_config.load_config().get("input_device") or "").strip()
        if configured:
            configured_lower = configured.lower()
            for index, info in enumerate(devices):
                name = str(info.get("name", ""))
                if int(info.get("max_input_channels", 0)) <= 0:
                    continue
                if configured == str(index) or configured_lower in name.lower():
                    return index, name or f"input {index}"
            raise RuntimeError(f"Configured input_device not found: {configured}")

        default_device = sd.default.device
        default_input = default_device[0] if isinstance(default_device, (list, tuple)) else default_device

        if isinstance(default_input, int) and default_input >= 0:
            try:
                info = sd.query_devices(default_input)
                if int(info.get("max_input_channels", 0)) > 0 and not self._is_virtual_input_device(info):
                    return default_input, str(info.get("name", "default input"))
            except Exception:
                pass

        fallback: tuple[int, str] | None = None
        for index, info in enumerate(devices):
            if int(info.get("max_input_channels", 0)) > 0:
                name = str(info.get("name", f"input {index}"))
                if not self._is_virtual_input_device(info):
                    return index, name
                if fallback is None:
                    fallback = (index, name)
        if fallback is not None:
            return fallback

        device_summary = "; ".join(
            f"{index}:{info.get('name')} inputs={info.get('max_input_channels')}"
            for index, info in enumerate(devices)
        )
        raise RuntimeError(f"No usable microphone input device found. Devices: {device_summary}")

    @staticmethod
    def _is_virtual_input_device(info: Any) -> bool:
        name = str(info.get("name", "")).lower()
        virtual_markers = (
            "microsoft teams audio",
            "zoom audio",
            "blackhole",
            "loopback",
            "soundflower",
            "aggregate",
            "multi-output",
            "多输出",
        )
        return any(marker in name for marker in virtual_markers)

    def _on_stream_audio(self, indata: Any, _frames: int, _time_info: Any, _status: Any) -> None:
        with self._audio_lock:
            self._audio_chunks.append(indata.copy())

    def _stop_streaming_audio(self) -> None:
        import numpy as np
        import soundfile as sf

        if self._audio_stream is None:
            return
        self._audio_stream.stop()
        self._audio_stream.close()
        self._audio_stream = None
        with self._audio_lock:
            chunks = list(self._audio_chunks)
        if not chunks:
            raise RuntimeError("Recording failed: no audio samples captured.")
        assert self._current_audio_path is not None
        audio = np.concatenate(chunks, axis=0)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak <= SILENT_PEAK_THRESHOLD:
            device = self._input_device_label or "unknown input device"
            raise RuntimeError(
                f"Recording captured only silence from {device}. "
                "Check macOS Microphone permission and the selected input device."
            )
        self._current_audio_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(self._current_audio_path, audio, 16_000)

    def _stream_transcribe_loop(self) -> None:
        model = None if self.backend in ("mlx", "azure") else self._ensure_model_loaded()
        last_sample_count = 0
        while True:
            processed_new, last_sample_count = self._process_stream_preview(model, last_sample_count)
            if self._stream_stop.is_set():
                if not processed_new:
                    self._process_stream_preview(model, last_sample_count, force=True)
                break
            time.sleep(1.25)

    def _process_stream_preview(self, model: WhisperModel | None, last_sample_count: int, *, force: bool = False) -> tuple[bool, int]:
        import numpy as np
        import soundfile as sf

        if self.backend == "azure":
            # Skip live previews for Azure to avoid per-chunk API billing; final text uses the full recording.
            return False, last_sample_count

        with self._audio_lock:
            chunks = list(self._audio_chunks)
        if not chunks:
            return False, last_sample_count

        audio = np.concatenate(chunks, axis=0)
        sample_count = int(audio.shape[0])
        min_delta = 16_000 * 2
        if not force and (sample_count < 16_000 * 3 or sample_count - last_sample_count < min_delta):
            return False, last_sample_count

        # Use a sliding preview window for responsiveness, but final output still uses the full recording.
        preview_audio = audio[-16_000 * 18 :]
        preview_dir = (self._recording_dir or Path(tempfile.gettempdir()) / "copilot-voice-shell") / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / "preview.wav"
        sf.write(preview_path, preview_audio, 16_000)
        if self.backend == "mlx":
            preview_result = transcribe_audio_mlx(
                preview_path,
                self.language,
                self.mlx_model,
                replacement_pairs=[],
                replacements_file=None,
            )
            preview_text_value = str(preview_result["plain_text"]).strip()
            texts = [preview_text_value] if preview_text_value else []
        else:
            assert model is not None
            segments, _info = model.transcribe(
                str(preview_path),
                language=self.language,
                condition_on_previous_text=False,
                beam_size=1,
            )
            texts = [segment.text.strip() for segment in segments if segment.text.strip()]
        if texts:
            preview_text = merge_segment_text(texts)
            self._report_status(
                {
                    "stage": "streaming",
                    "audio_path": str(self._current_audio_path or preview_path),
                    "plain_text": preview_text,
                    "raw_text": preview_text,
                    "rephrased_text": "",
                    "error": "Preview only; final text is computed from the full recording.",
                }
            )
        return True, sample_count

    def _transcribe_with_loaded_model(self, audio_path: Path) -> dict[str, object]:
        if self.backend == "mlx":
            return transcribe_audio_mlx(
                audio_path,
                self.language,
                self.mlx_model,
                replacement_pairs=self.replacement_pairs,
                replacements_file=self.replacements_file,
            )
        if self.backend == "azure":
            return transcribe_audio_azure(
                audio_path,
                self.language,
                replacement_pairs=self.replacement_pairs,
                replacements_file=self.replacements_file,
                language_preference=self.language_preference,
            )
        model = self._ensure_model_loaded()
        segments, info = model.transcribe(str(audio_path), language=self.language)
        replacement_map = load_replacements(self.replacements_file, self.replacement_pairs)
        segment_lines: list[SegmentLine] = []
        raw_lines: list[str] = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            raw_lines.append(text)
            segment_lines.append(
                SegmentLine(
                    start=segment.start,
                    end=segment.end,
                    text=apply_replacements(text, replacement_map),
                )
            )
        plain_text = merge_segment_text(line.text for line in segment_lines)
        return {
            "info": info,
            "segments": segment_lines,
            "plain_text": plain_text,
            "raw_text": merge_segment_text(raw_lines),
            "replacement_map": replacement_map,
        }

    def _build_streaming_result(self) -> dict[str, object]:
        replacement_map = load_replacements(self.replacements_file, self.replacement_pairs)
        rewritten_segments = [
            SegmentLine(segment.start, segment.end, apply_replacements(segment.text, replacement_map))
            for segment in self._streamed_segments
        ]
        plain_text = merge_segment_text(segment.text for segment in rewritten_segments)
        raw_text = merge_segment_text(segment.text for segment in self._streamed_segments)
        return {
            "info": RecognitionInfo(language=self.language, language_probability=1.0),
            "segments": rewritten_segments,
            "plain_text": plain_text,
            "raw_text": raw_text,
            "replacement_map": replacement_map,
        }

    def _report_status(self, update: dict[str, object]) -> None:
        if self.status_reporter is not None:
            self.status_reporter(update)

    def _session_context_status(self) -> str:
        if not self.session_context:
            return ""
        if self._session_context_id:
            return f"Copilot session: {self._session_context_id[:8]}..."
        return "Copilot session: not found"


def normalize_hotkey(hotkey: str) -> str:
    parts = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
    if not parts:
        raise SystemExit("Hotkey cannot be empty.")

    aliases = {
        "cmd": "<cmd>",
        "command": "<cmd>",
        "ctrl": "<ctrl>",
        "control": "<ctrl>",
        "alt": "<alt>",
        "option": "<alt>",
        "shift": "<shift>",
        "space": "<space>",
        "enter": "<enter>",
        "return": "<enter>",
    }

    normalized: list[str] = []
    for part in parts:
        normalized.append(aliases.get(part, part if len(part) == 1 else f"<{part}>"))
    return "+".join(normalized)


def load_replacements(replacements_file: Path | None, replacement_pairs: Sequence[str]) -> dict[str, str]:
    replacements: dict[str, str] = {}

    if replacements_file is not None:
        data = json.loads(replacements_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit("Replacement file must contain a JSON object.")
        for key, value in data.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SystemExit("Replacement file must map strings to strings.")
            replacements[key] = value

    for pair in replacement_pairs:
        source, target = parse_replacement_pair(pair)
        replacements[source] = target

    return replacements


def parse_replacement_pair(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise SystemExit(f"Invalid replacement '{value}'. Expected FROM=TO.")
    source, target = value.split("=", 1)
    source = source.strip()
    target = target.strip()
    if not source:
        raise SystemExit(f"Invalid replacement '{value}'. FROM cannot be empty.")
    return source, target


def apply_replacements(text: str, replacements: dict[str, str]) -> str:
    updated = text
    for source, target in replacements.items():
        updated = updated.replace(source, target)
    return updated


def merge_segment_text(segments: Iterable[str]) -> str:
    parts = [segment.strip() for segment in segments if segment.strip()]
    return " ".join(parts)


def copy_text(text: str) -> None:
    import pyperclip

    try:
        pyperclip.copy(text)
    except pyperclip.PyperclipException as exc:  # pragma: no cover - platform clipboard missing
        raise SystemExit(f"Clipboard copy failed: {exc}")
    copied = pyperclip.paste() or ""
    if copied.rstrip("\n") != text.rstrip("\n"):
        raise SystemExit("Clipboard copy verification failed: clipboard content does not match the transcription text.")


def paste_from_clipboard(*, submit: bool = False, target_app: AppTarget | None = None) -> None:
    from .platform_services import FocusInfo, get_platform_services

    svc = get_platform_services()
    if target_app is not None:
        svc.restore_focus(FocusInfo(
            name=target_app.name,
            bundle_id=target_app.bundle_id,
            pid=target_app.pid,
        ))
    svc.paste_keystroke(submit=submit)


def send_text_to_active_app(text: str, *, submit: bool) -> None:
    target_app = get_frontmost_app_info()
    copy_text(text)
    print(f"Copied text to clipboard. Attempting paste into frontmost app: {target_app.name}")
    paste_from_clipboard(submit=submit, target_app=target_app)
    print(f"Paste keystroke sent to: {target_app.name}" + (" (with submit)" if submit else ""))


def resolve_send_text(text_arg: str | None, from_file: Path | None) -> str:
    if from_file is not None:
        return from_file.read_text(encoding="utf-8").rstrip("\n")
    if text_arg is not None:
        return text_arg
    if not sys.stdin.isatty():
        return sys.stdin.read().rstrip("\n")
    raise SystemExit("No text provided. Pass text, --from-file, or pipe stdin into the send command.")


def get_frontmost_app_info() -> AppTarget:
    from .platform_services import get_platform_services

    info = get_platform_services().get_frontmost_window()
    if info is None:
        return AppTarget(name="active window", bundle_id="", pid=0)
    return AppTarget(name=info.name, bundle_id=info.bundle_id, pid=info.pid)


def run_doctor() -> None:
    import importlib.util

    def _mod(name: str) -> str:
        return "ok" if importlib.util.find_spec(name) is not None else "missing"

    print(f"Platform: {sys.platform}")
    print(f"Python: {sys.executable}")
    print(f"ffmpeg: {shutil.which('ffmpeg') or 'missing (optional)'}")
    print(f"sounddevice: {_mod('sounddevice')}")
    print(f"soundfile: {_mod('soundfile')}")
    print(f"pyperclip (clipboard): {_mod('pyperclip')}")
    print(f"pynput (paste/hotkey): {_mod('pynput')}")
    print(f"faster-whisper: {_mod('faster_whisper')}")
    if sys.platform == "darwin":
        print(f"mlx-whisper: {_mod('mlx_whisper')}")
        print(f"osascript: {shutil.which('osascript') or 'missing'}")
    print(f"ollama: {shutil.which('ollama') or 'missing'}")
    print(f"HF_ENDPOINT: {os.environ.get('HF_ENDPOINT', DEFAULT_HF_ENDPOINT)}")
    print("Default language:", DEFAULT_LANGUAGE)
    print("Default backend:", DEFAULT_BACKEND)
    print("Default model:", DEFAULT_MODEL)
    print("Default MLX model:", DEFAULT_MLX_MODEL)
    print("Default polish engine:", DEFAULT_POLISH_ENGINE)
    print("Default Ollama model:", DEFAULT_OLLAMA_MODEL)
    print("Default hotkey:", DEFAULT_HOTKEY)


def default_recording_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    recordings_dir = project_root / "recordings"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return recordings_dir / f"recording-{timestamp}.m4a"


def default_hotkey_recording_path() -> Path:
    temp_dir = Path(tempfile.gettempdir()) / "copilot-voice-shell"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return temp_dir / f"hotkey-recording-{timestamp}.m4a"


def ensure_command(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Required command not found in PATH: {name}")
