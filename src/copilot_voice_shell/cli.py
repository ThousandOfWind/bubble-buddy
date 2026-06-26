from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from faster_whisper import WhisperModel
from faster_whisper.utils import download_model

DEFAULT_LANGUAGE = "zh"
DEFAULT_MODEL = "small"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"


@dataclass(frozen=True)
class SegmentLine:
    start: float
    end: float
    text: str


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
        "--copy",
        action="store_true",
        help="Copy the plain transcription text to the macOS clipboard.",
    )
    parser.add_argument(
        "--paste",
        action="store_true",
        help="Paste the plain transcription into the active macOS app after copying it.",
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
            paste_to_active_app=args.paste,
            plain=args.plain,
            save_text=args.save_text,
            hf_endpoint=args.hf_endpoint,
            replacement_pairs=args.replace,
            replacements_file=args.replacements_file,
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
            args.hf_endpoint,
            replacement_pairs=args.replace,
            replacements_file=args.replacements_file,
        )
        emit_transcription(
            result,
            plain=args.plain,
            copy_to_clipboard=args.copy,
            paste_to_active_app=args.paste,
            save_text=args.save_text,
        )
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
    copy_to_clipboard: bool,
    paste_to_active_app: bool,
    plain: bool,
    save_text: Path | None,
    hf_endpoint: str,
    replacement_pairs: Sequence[str],
    replacements_file: Path | None,
) -> None:
    audio_path = record_audio(output)
    result = transcribe_audio(
        audio_path,
        language,
        model_name,
        hf_endpoint,
        replacement_pairs=replacement_pairs,
        replacements_file=replacements_file,
    )
    emit_transcription(
        result,
        plain=plain,
        copy_to_clipboard=copy_to_clipboard,
        paste_to_active_app=paste_to_active_app,
        save_text=save_text,
    )


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


def transcribe_audio(
    audio: Path,
    language: str,
    model_name: str,
    hf_endpoint: str,
    *,
    replacement_pairs: Sequence[str],
    replacements_file: Path | None,
) -> dict[str, object]:
    if not audio.exists():
        raise SystemExit(f"Audio file not found: {audio}")

    os.environ["HF_ENDPOINT"] = hf_endpoint

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


def emit_transcription(
    result: dict[str, object],
    *,
    plain: bool,
    copy_to_clipboard: bool,
    paste_to_active_app: bool,
    save_text: Path | None,
) -> None:
    plain_text = result["plain_text"]
    assert isinstance(plain_text, str)

    if plain:
        print(plain_text)
    else:
        print(format_verbose_output(result))

    if save_text is not None:
        save_text.parent.mkdir(parents=True, exist_ok=True)
        save_text.write_text(plain_text + "\n", encoding="utf-8")
        print(f"\nSaved text to {save_text}")

    if copy_to_clipboard or paste_to_active_app:
        copy_text(plain_text)
        print("\nCopied plain transcription to clipboard.")
    if paste_to_active_app:
        paste_from_clipboard()
        print("Pasted into the active app.")


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
    os.environ["HF_ENDPOINT"] = hf_endpoint
    return download_model(model_name)


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
    if sys.platform != "darwin":
        raise SystemExit("Clipboard copy is only implemented for macOS right now.")

    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def paste_from_clipboard() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Clipboard paste is only implemented for macOS right now.")

    ensure_command("osascript")
    script = 'tell application "System Events" to keystroke "v" using command down'
    subprocess.run(["osascript", "-e", script], check=True)


def run_doctor() -> None:
    print(f"Python: {sys.executable}")
    print(f"ffmpeg: {shutil.which('ffmpeg') or 'missing'}")
    print(f"pbcopy: {shutil.which('pbcopy') or 'missing'}")
    print(f"osascript: {shutil.which('osascript') or 'missing'}")
    print(f"HF_ENDPOINT: {os.environ.get('HF_ENDPOINT', DEFAULT_HF_ENDPOINT)}")
    print("Default language:", DEFAULT_LANGUAGE)
    print("Default model:", DEFAULT_MODEL)


def default_recording_path() -> Path:
    project_root = Path(__file__).resolve().parents[2]
    recordings_dir = project_root / "recordings"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return recordings_dir / f"recording-{timestamp}.m4a"


def ensure_command(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"Required command not found in PATH: {name}")
