from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from faster_whisper import WhisperModel

DEFAULT_LANGUAGE = "zh"
DEFAULT_MODEL = "small"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"


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

    subparsers.add_parser(
        "doctor",
        help="Check local prerequisites for recording and transcription.",
    )

    return parser


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="Language hint, e.g. zh or en.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Whisper model name, e.g. small or medium.")
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy the final transcription to the macOS clipboard.",
    )
    parser.add_argument(
        "--hf-endpoint",
        default=os.environ.get("HF_ENDPOINT", DEFAULT_HF_ENDPOINT),
        help="Model download endpoint for Hugging Face-compatible downloads.",
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command or "capture"
    if command == "capture":
        run_capture(
            output=args.output,
            language=args.language,
            model_name=args.model,
            copy_to_clipboard=args.copy,
            hf_endpoint=args.hf_endpoint,
        )
        return
    if command == "record":
        path = record_audio(args.output)
        print(path)
        return
    if command == "transcribe":
        transcript = transcribe_audio(args.audio, args.language, args.model, args.hf_endpoint)
        print(transcript)
        if args.copy:
            copy_text(transcript)
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
    copy_to_clipboard: bool,
    hf_endpoint: str,
) -> None:
    audio_path = record_audio(output)
    transcript = transcribe_audio(audio_path, language, model_name, hf_endpoint)
    print(transcript)
    if copy_to_clipboard:
        copy_text(transcript)
        print("\nCopied transcription to clipboard.")


def record_audio(output: Path | None) -> Path:
    ensure_command("ffmpeg")

    audio_path = output or default_recording_path()
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform != "darwin":
        raise SystemExit("Recording is only implemented for macOS right now.")

    print("Press Enter to start recording.")
    input()

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "avfoundation",
        "-i",
        ":0",
        "-c:a",
        "aac",
        str(audio_path),
    ]
    process = subprocess.Popen(command)
    try:
        print("Recording... Press Enter to stop.")
        input()
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        process.wait()

    print(f"Saved audio to {audio_path}")
    return audio_path


def transcribe_audio(audio: Path, language: str, model_name: str, hf_endpoint: str) -> str:
    if not audio.exists():
        raise SystemExit(f"Audio file not found: {audio}")

    os.environ["HF_ENDPOINT"] = hf_endpoint

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(audio), language=language)

    lines = [f"Detected language: {info.language} (prob={info.language_probability:.3f})"]
    for segment in segments:
        lines.append(f"[{segment.start:.2f}-{segment.end:.2f}] {segment.text.strip()}")
    return "\n".join(lines)


def copy_text(text: str) -> None:
    if sys.platform != "darwin":
        raise SystemExit("Clipboard copy is only implemented for macOS right now.")

    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def run_doctor() -> None:
    print(f"Python: {sys.executable}")
    print(f"ffmpeg: {shutil.which('ffmpeg') or 'missing'}")
    print(f"pbcopy: {shutil.which('pbcopy') or 'missing'}")
    print(f"HF_ENDPOINT: {os.environ.get('HF_ENDPOINT', DEFAULT_HF_ENDPOINT)}")
    print("Default language:", DEFAULT_LANGUAGE)
    print("Default model:", DEFAULT_MODEL)


def default_recording_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    recordings_dir = project_root / "recordings"
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return recordings_dir / f"recording-{timestamp}.m4a"


def ensure_command(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Required command not found in PATH: {name}")
