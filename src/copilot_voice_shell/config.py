from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

# Built-in defaults. A user config.json can override any of these.
DEFAULTS: dict[str, Any] = {
    "language": "zh",
    "model": "small",
    "backend": "faster-whisper",  # faster-whisper | mlx | azure
    "mlx_model": "mlx-community/whisper-large-v3-turbo",
    "hotkey": "f9",
    "hf_endpoint": "https://hf-mirror.com",
    "polish": "off",  # off | copilot
    "polish_engine": "rules",  # rules | ollama | azure
    "ollama_model": "qwen3:latest",
    "language_preference": "zh-en",
    "max_record_seconds": 120,  # auto-stop after this many seconds (0 = no limit)
    "azure": {
        "endpoint": "",  # e.g. https://<resource>.cognitiveservices.azure.com/
        "api_version": "2025-03-01-preview",
        "auth": "aad",  # aad (use az login user credential) | api_key
        "api_key_env": "AZURE_OPENAI_API_KEY",
        "api_key": "",  # optional: paste key here (config.json is gitignored)
        "scope": "https://cognitiveservices.azure.com/.default",
        "transcribe_deployment": "gpt-4o-transcribe",
        "chat_deployment": "gpt-4.1",
        "transcribe_mode": "batch",  # batch | stream | realtime
        "realtime_api_version": "2025-04-01-preview",
        "stream": True,
    },
}

_CACHE: dict[str, Any] | None = None


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env = os.environ.get("COPILOT_VOICE_SHELL_CONFIG")
    if env:
        paths.append(Path(env))
    paths.append(Path.cwd() / "config.json")
    paths.append(Path(__file__).resolve().parents[2] / "config.json")
    paths.append(Path.home() / ".copilot-voice-shell" / "config.json")
    return paths


def load_config(reload: bool = False) -> dict[str, Any]:
    """Load merged configuration (built-in defaults overridden by the first
    config.json found in COPILOT_VOICE_SHELL_CONFIG, cwd, project root, or home)."""
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE

    cfg = copy.deepcopy(DEFAULTS)
    for path in _candidate_paths():
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        azure = {**cfg["azure"], **(data.get("azure") or {})}
        for key, value in data.items():
            if key == "azure":
                continue
            cfg[key] = value
        cfg["azure"] = azure
        cfg["_source"] = str(path)
        break

    _CACHE = cfg
    return cfg


def get_azure_config() -> dict[str, Any]:
    return load_config()["azure"]


def config_path_for_write() -> Path:
    """Return the config file to write to: the first existing candidate, or the
    project-root config.json if none exists yet."""
    for path in _candidate_paths():
        if path.is_file():
            return path
    return Path(__file__).resolve().parents[2] / "config.json"


def save_config(updates: dict[str, Any]) -> Path:
    """Merge ``updates`` into the on-disk config.json and reload the cache.

    ``updates`` may contain top-level keys and a nested ``azure`` dict; only the
    provided keys are changed. Internal keys (starting with ``_``) are ignored.
    Returns the path written."""
    path = config_path_for_write()
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                existing = data
        except (OSError, json.JSONDecodeError):
            existing = {}

    for key, value in updates.items():
        if key.startswith("_"):
            continue
        if key == "azure" and isinstance(value, dict):
            merged_azure = {**(existing.get("azure") or {}), **value}
            existing["azure"] = merged_azure
        else:
            existing[key] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_config(reload=True)
    return path
