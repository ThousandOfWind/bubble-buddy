"""Frozen-app entry point.

When the packaged executable is double-clicked with no arguments we launch the
Qt desktop overlay (the click-to-use experience). Any explicit CLI arguments are
still forwarded to the normal CLI so ``copilot-voice-shell.exe transcribe ...``
keeps working.
"""

import multiprocessing
import json
import os
import shutil
import sys
from pathlib import Path


def _bundled_config_path() -> Path | None:
    roots = [
        Path(getattr(sys, "_MEIPASS", "")),
        Path(sys.executable).resolve().parent,
        Path(sys.executable).resolve().parent.parent / "Resources",
        Path(sys.executable).resolve().parent.parent / "Frameworks",
    ]
    for root in roots:
        if not str(root):
            continue
        path = root / "config.json"
        if path.is_file():
            return path
    return None


def _bundled_model_path() -> Path | None:
    roots = [
        Path(getattr(sys, "_MEIPASS", "")),
        Path(sys.executable).resolve().parent,
        Path(sys.executable).resolve().parent.parent / "Resources",
        Path(sys.executable).resolve().parent.parent / "Frameworks",
    ]
    for root in roots:
        if not str(root):
            continue
        path = root / "models" / "mlx-whisper-large-v3-turbo"
        if (path / "config.json").is_file() and (path / "weights.safetensors").is_file():
            return path
    return None


def _write_seed_config(bundled: Path, target: Path) -> None:
    text = bundled.read_text(encoding="utf-8")
    if "__BUNDLED_MLX_MODEL__" in text:
        model_path = _bundled_model_path()
        if model_path is not None:
            data = json.loads(text)
            data["mlx_model"] = str(model_path)
            text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    target.write_text(text, encoding="utf-8")


def _seed_packaged_user_config() -> None:
    if not getattr(sys, "frozen", False) or os.environ.get("COPILOT_VOICE_SHELL_CONFIG"):
        return
    user_config = Path.home() / ".copilot-voice-shell" / "config.json"
    bundled = _bundled_config_path()
    if bundled is not None and not user_config.exists():
        user_config.parent.mkdir(parents=True, exist_ok=True)
        _write_seed_config(bundled, user_config)
    os.environ["COPILOT_VOICE_SHELL_CONFIG"] = str(user_config)


def _run() -> None:
    _seed_packaged_user_config()

    from copilot_voice_shell import main

    argv = sys.argv[1:]
    if not argv:
        argv = ["desktop"]
    main(argv)


if __name__ == "__main__":
    # Required so PyInstaller-frozen apps don't re-launch the GUI in worker
    # subprocesses spawned by libraries that use multiprocessing.
    multiprocessing.freeze_support()
    _run()
