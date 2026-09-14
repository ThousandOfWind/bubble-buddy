"""Real fixture audio -> faster-whisper -> polish -> application file delivery.

Default: cached local model + rules. --live-copilot opts into two possible paid
text calls using an existing login. No mocks, microphone, clipboard, app focus,
private context or automatic sign-in. See docs/audio-e2e.md.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "audio"


def load_fixtures() -> list[dict]:
    return json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))["fixtures"]


def verify_fixture(fixture: dict) -> Path:
    name = fixture["file"]
    if Path(name).name != name or not name.endswith(".wav"):
        raise ValueError("Fixture must be a WAV basename inside the fixture directory")
    path = FIXTURES / name
    data = path.read_bytes()
    if len(data) != fixture["bytes"] or hashlib.sha256(data).hexdigest() != fixture["sha256"]:
        raise ValueError(f"Fixture integrity mismatch: {name}")
    with wave.open(str(path)) as audio:
        observed = (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getnframes())
        expected = tuple(fixture[key] for key in ("sample_rate", "channels", "sample_width", "frames"))
        if observed != expected or not any(audio.readframes(audio.getnframes())):
            raise ValueError(f"Unexpected or silent WAV data: {name}")
    return path


def tokens(text: str, metric: str) -> list[str]:
    if metric == "wer":
        return re.findall(r"[a-z0-9]+", text.casefold())
    if metric == "cer":
        return [char for char in text.casefold() if char.isalnum()]
    raise ValueError(f"Unsupported fixture metric: {metric}")


def error_rate(reference: str, actual: str, metric: str) -> float:
    expected, observed = tokens(reference, metric), tokens(actual, metric)
    if not expected:
        raise ValueError("Empty reference transcript")
    previous = list(range(len(observed) + 1))
    for i, expected_token in enumerate(expected, 1):
        current = [i]
        for j, observed_token in enumerate(observed, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (expected_token != observed_token)))
        previous = current
    return previous[-1] / len(expected)


def assess(fixture: dict, text: str, stage: str) -> dict:
    references = [fixture["transcript"], *fixture.get("reference_variants", [])]
    rate = min(error_rate(reference, text, fixture["metric"]) for reference in references)
    missing = [alternatives for alternatives in fixture["required_phrases"]
               if not any(phrase.casefold() in text.casefold() for phrase in alternatives)]
    if stage not in ("asr", "polish"):
        raise ValueError(f"Unsupported assessment stage: {stage}")
    limit = fixture[f"max_{stage}_error_rate"]
    # ASR can contain small phonetic mistakes: correcting them is the next
    # stage's job. Record missing raw phrases, but enforce them on final output.
    return {"metric": fixture["metric"], "error_rate": round(rate, 6), "limit": limit,
            "missing_phrases": missing,
            "passed": bool(text.strip()) and rate <= limit and (stage == "asr" or not missing)}


def run(args: argparse.Namespace) -> dict:
    sys.path.insert(0, str(REPO / "src"))
    from bubble_buddy import cli, config, copilot_client
    from faster_whisper.utils import download_model

    polish_model = args.polish_model or config.DEFAULTS["copilot_model"]
    output = Path(args.output_dir) if args.output_dir else Path(tempfile.mkdtemp(prefix="bb-audio-e2e-"))
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "status": "running", "mode": "copilot" if args.live_copilot else "rules",
              "scope": "audio file -> local ASR -> polish -> application text-file delivery",
              "microphone_tested": False, "clipboard_or_os_paste_tested": False,
              "model": polish_model if args.live_copilot else "rules", "cases": []}
    report_path = output / "report.json"

    def save_report() -> None:
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, report_path)

    started = time.perf_counter()
    previous_config = config._CACHE
    try:
        save_report()
        fixtures = load_fixtures()
        for fixture in fixtures:
            verify_fixture(fixture)
        # Isolate config without reading/writing user settings or injecting live
        # session/window context. Normal application functions are used unchanged.
        profile = copy.deepcopy(config.DEFAULTS)
        profile.update({"backend": "faster-whisper", "polish": "copilot", "polish_engine": report["mode"],
                        "copilot_model": polish_model})
        config._CACHE = profile
        model_path = Path(args.asr_model).expanduser()
        if not model_path.is_dir():
            model_path = Path(download_model(args.asr_model, local_files_only=not args.download_model))
        report["asr_model"] = str(model_path)
        if args.live_copilot:
            if not copilot_client.auth_status()["signed_in"]:
                raise RuntimeError("Run `uv run bubble-buddy auth login copilot` first; E2E never opens a login browser.")
            if polish_model not in copilot_client.list_models():
                raise RuntimeError("Requested Copilot model is not enabled/compatible; no alternative is substituted.")
        report["preflight_seconds"] = round(time.perf_counter() - started, 3)
        for fixture in fixtures:
            case = {"id": fixture["id"], "audio_seconds": fixture["frames"] / fixture["sample_rate"], "status": "running"}
            report["cases"].append(case)
            case_start = time.perf_counter()
            stage = "asr"
            try:
                save_report()
                raw = cli.transcribe_audio(
                    FIXTURES / fixture["file"], fixture["language"], str(model_path),
                    "faster-whisper", "", "https://huggingface.co",
                    replacement_pairs=[], replacements_file=None,
                )
                case["asr_seconds"] = round(time.perf_counter() - case_start, 3)
                case["raw_text"] = str(raw["raw_text"])
                case["asr_check"] = assess(fixture, case["raw_text"], "asr")
                if not case["asr_check"]["passed"]:
                    raise AssertionError("ASR failed the reference error-rate check; cloud polish was not called.")
                stage = "polish"
                save_report()
                polish_start = time.perf_counter()
                polished = cli.apply_polish_to_result(
                    raw, "copilot", None, False, fixture["language"], report["mode"], "unused",
                )
                case["polish_seconds"] = round(time.perf_counter() - polish_start, 3)
                case["polished_text"] = str(polished["plain_text"])
                case["polish_check"] = assess(fixture, case["polished_text"], "polish")
                if not case["polish_check"]["passed"]:
                    raise AssertionError("Polished output failed reference/phrase checks.")
                stage = "delivery"
                destination = output / f"{fixture['id']}.txt"
                outcome = cli.emit_transcription(
                    polished, plain=True, copy_to_clipboard=False, paste_to_active_app=False,
                    submit_to_active_app=False, save_text=destination,
                )
                if destination.read_text(encoding="utf-8") != case["polished_text"] + "\n":
                    raise AssertionError("Delivered text file does not match the final text")
                if any(outcome[key] for key in ("copied", "pasted", "submitted")):
                    raise AssertionError("E2E must not interact with the system clipboard or other apps")
                case.update(status="passed", delivered_file=destination.name)
            except Exception as exc:
                case.update(status="failed", failed_stage=stage, error=str(exc), error_type=type(exc).__name__)
            case["total_seconds"] = round(time.perf_counter() - case_start, 3)
            save_report()
        report["status"] = "passed" if report["cases"] and all(case["status"] == "passed" for case in report["cases"]) else "failed"
    except Exception as exc:
        report.update(status="failed", failed_stage="preflight", error=str(exc), error_type=type(exc).__name__)
    finally:
        config._CACHE = previous_config
        report["total_seconds"] = round(time.perf_counter() - started, 3)
        save_report()
    print(f"Audio E2E: {report['status']} — {report_path}")
    return report


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-copilot", action="store_true", help="Opt in to real Copilot calls (up to one per fixture; plan usage applies).")
    parser.add_argument("--asr-model", default="small", help="Cached faster-whisper model name or local directory.")
    parser.add_argument("--download-model", action="store_true", help="Allow a missing model download; weights stay outside the repo.")
    parser.add_argument("--polish-model", help="Copilot model to verify; defaults to the application's recommendation, never silently substituted.")
    parser.add_argument("--output-dir", help="Report/text output directory; defaults to a new temporary directory.")
    args = parser.parse_args()
    raise SystemExit(0 if run(args)["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
