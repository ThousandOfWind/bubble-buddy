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
    meipass = getattr(sys, "_MEIPASS", "")
    roots = [
        Path(sys.executable).resolve().parent,
        Path(sys.executable).resolve().parent.parent / "Resources",
        Path(sys.executable).resolve().parent.parent / "Frameworks",
    ]
    if isinstance(meipass, str) and meipass:
        roots.insert(0, Path(meipass))
    for root in roots:
        if not root.is_absolute():
            continue
        path = root / "config.json"
        if path.is_file():
            return path
    return None


def _seed_packaged_user_config() -> None:
    if not getattr(sys, "frozen", False) or os.environ.get("COPILOT_VOICE_SHELL_CONFIG"):
        return
    user_config = Path.home() / ".copilot-voice-shell" / "config.json"
    bundled = _bundled_config_path()
    if bundled is not None:
        user_config.parent.mkdir(parents=True, exist_ok=True)
        if not user_config.exists():
            shutil.copyfile(bundled, user_config)
        else:
            _merge_packaged_defaults(user_config, bundled)
    os.environ["COPILOT_VOICE_SHELL_CONFIG"] = str(user_config)


def _merge_packaged_defaults(user_config: Path, bundled: Path) -> None:
    try:
        existing = json.loads(user_config.read_text(encoding="utf-8"))
        defaults = json.loads(bundled.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(existing, dict) or not isinstance(defaults, dict):
        return

    changed = False
    for key, value in defaults.items():
        if key == "azure" and isinstance(value, dict):
            azure = existing.setdefault("azure", {})
            if isinstance(azure, dict):
                for sub_key, sub_value in value.items():
                    if sub_key not in azure:
                        azure[sub_key] = sub_value
                        changed = True
            continue
        if key not in existing:
            existing[key] = value
            changed = True

    # Upgrade path for users who tested an earlier Full DMG before mlx_model and
    # setup-on-first-launch existed. Keep explicit user choices, but if local MLX is
    # selected without a model, seed the packaged repo id and show setup once.
    if existing.get("backend") == "mlx" and not str(existing.get("mlx_model") or "").strip():
        model = str(defaults.get("mlx_model") or "").strip()
        if model:
            existing["mlx_model"] = model
            existing["show_setup_on_first_launch"] = True
            changed = True
    if "show_setup_on_first_launch" not in existing and defaults.get("show_setup_on_first_launch"):
        existing["show_setup_on_first_launch"] = True
        changed = True

    if changed:
        user_config.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run() -> None:
    _seed_packaged_user_config()

    from copilot_voice_shell import main
    from copilot_voice_shell.platform_services import suppress_child_console_windows

    # Windowed app: stop child console programs (az.cmd, pwsh, ollama, ...) from
    # flashing a black console window.
    suppress_child_console_windows()

    argv = sys.argv[1:]
    if not argv:
        argv = ["desktop"]
    main(argv)


if __name__ == "__main__":
    # Required so PyInstaller-frozen apps don't re-launch the GUI in worker
    # subprocesses spawned by libraries that use multiprocessing.
    multiprocessing.freeze_support()
    _run()
