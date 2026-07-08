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
    "input_device": "",  # optional sounddevice input index or name substring
    "hf_endpoint": "https://hf-mirror.com",
    "polish": "off",  # off | copilot
    "polish_engine": "rules",  # rules | ollama | azure
    "ollama_model": "qwen3:latest",
    "polish_prompts": {},  # legacy per-mode prompt overrides: {"dev": "...", ...}
    "polish_categories": [],  # user-editable categories; filled from built-ins on load
    "language_preference": "zh-en",
    "ui_language": "auto",  # auto | zh | en — language of the overlay UI itself
    "first_launch_done": False,  # set True after the one-time greeting bubble shows
    "show_setup_on_first_launch": False,  # packaged app opens Settings once
    "start_collapsed": True,  # start as the compact pet/orb; click to expand
    "max_record_seconds": 120,  # auto-stop after this many seconds (0 = no limit)
    # Output / delivery of the final text. CLI flags (--copy/--paste/--submit) can
    # force any of these on at launch; the settings panel edits the persisted values.
    "copy_to_clipboard": False,  # copy the final text to the system clipboard
    "paste_to_active_app": True,  # auto-paste into the focused app (复制到光标)
    "submit_to_active_app": False,  # press Enter after pasting (implies paste)
    "launch_at_startup": False,  # register the app to start automatically on login
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
    },
}

_CACHE: dict[str, Any] | None = None


def _normalize_polish_engine(value: Any) -> Any:
    if isinstance(value, str) and value.strip().lower() == "rule":
        return "rules"
    return value


def default_polish_categories() -> list[dict[str, Any]]:
    """A deep copy of the built-in polish categories. Lazily imported to avoid a
    circular import with polish.py."""
    try:
        from .polish import BUILTIN_CATEGORIES

        return copy.deepcopy(BUILTIN_CATEGORIES)
    except Exception:
        return []


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
        app = data.get("app") or {}
        if isinstance(app, dict):
            for src in ("ui_language", "hotkey", "input_device", "start_collapsed", "show_setup_on_first_launch"):
                if src in app and src not in data:
                    cfg[src] = app[src]
        speech = data.get("speech") or {}
        if isinstance(speech, dict):
            for src, dst in (
                ("backend", "backend"),
                ("language", "language"),
                ("language_preference", "language_preference"),
                ("max_record_seconds", "max_record_seconds"),
            ):
                if src in speech and dst not in data:
                    cfg[dst] = speech[src]
        explicit_backend = "backend" in data or (isinstance(speech, dict) and "backend" in speech)
        model_from_local = False
        mlx_model_group = data.get("mlx_model") if isinstance(data.get("mlx_model"), dict) else {}
        if isinstance(mlx_model_group, dict):
            if str(mlx_model_group.get("type") or "mlx").strip().lower() == "mlx" and not explicit_backend:
                cfg["backend"] = "mlx"
            if mlx_model_group.get("path"):
                cfg["mlx_model"] = mlx_model_group["path"]
            elif mlx_model_group.get("repo"):
                cfg["mlx_model"] = mlx_model_group["repo"]
            if mlx_model_group.get("hf_endpoint"):
                cfg["hf_endpoint"] = mlx_model_group["hf_endpoint"]

        local_model = data.get("local_model") or {}
        if isinstance(local_model, dict):
            local_type = str(local_model.get("type") or "").strip().lower()
            if local_type in ("mlx", "faster-whisper") and not explicit_backend:
                cfg["backend"] = local_type
            for src, dst in (
                ("path", "mlx_model"),
                ("install_dir", "mlx_model"),
                ("mlx_model", "mlx_model"),
            ):
                if local_type in ("", "mlx") and local_model.get(src) and dst not in data:
                    cfg[dst] = local_model[src]
            if local_type == "faster-whisper":
                if local_model.get("path") and "model" not in data:
                    cfg["model"] = local_model["path"]
                    model_from_local = True
                elif local_model.get("faster_whisper_path") and "model" not in data:
                    cfg["model"] = local_model["faster_whisper_path"]
                    model_from_local = True
        faster_whisper = data.get("faster_whisper_model") or data.get("faster_whisper") or {}
        if isinstance(faster_whisper, dict) and not model_from_local:
            if str(faster_whisper.get("type") or "").strip().lower() == "faster-whisper" and not explicit_backend:
                cfg["backend"] = "faster-whisper"
            if cfg.get("backend") != "faster-whisper":
                pass
            elif "path" in faster_whisper and "model" not in data:
                cfg["model"] = faster_whisper["path"]
                model_from_local = True
            elif "model" in faster_whisper and "model" not in data:
                cfg["model"] = faster_whisper["model"]
                model_from_local = True
            elif "repo" in faster_whisper and "model" not in data:
                cfg["model"] = faster_whisper["repo"]
                model_from_local = True
            if "hf_endpoint" in faster_whisper and "hf_endpoint" not in data:
                cfg["hf_endpoint"] = faster_whisper["hf_endpoint"]
        if "mlx_model" in data and isinstance(data.get("mlx_model"), str):
            cfg["mlx_model"] = data["mlx_model"]
        elif "mlx_model" in data and isinstance(data.get("mlx_model"), dict):
            # Already handled above; skip the generic top-level merge below.
            pass
        model_download = data.get("model_download") or {}
        if isinstance(model_download, dict):
            if "hf_endpoint" in model_download and "hf_endpoint" not in data:
                cfg["hf_endpoint"] = model_download["hf_endpoint"]
            if (
                "faster_whisper_repo" in model_download
                and "model" not in data
                and not model_from_local
                and cfg.get("backend") == "faster-whisper"
            ):
                cfg["model"] = model_download["faster_whisper_repo"]
            # If no local model path is configured yet, use the repo id as a
            # download-capable fallback for MLX. Otherwise runtime uses the path.
            repo = model_download.get("repo") or model_download.get("mlx_repo")
            if not cfg.get("mlx_model") and repo and "mlx_model" not in data:
                cfg["mlx_model"] = repo
        ollama = data.get("ollama") or {}
        if isinstance(ollama, dict):
            if "ollama_model" in ollama and "ollama_model" not in data:
                cfg["ollama_model"] = ollama["ollama_model"]
            elif "model" in ollama and "ollama_model" not in data:
                cfg["ollama_model"] = ollama["model"]
        polish = data.get("polish")
        if isinstance(polish, dict):
            if "mode" in polish:
                cfg["polish"] = polish["mode"]
            elif "category" in polish:
                cfg["polish"] = polish["category"]
            if "engine" in polish and "polish_engine" not in data:
                cfg["polish_engine"] = _normalize_polish_engine(polish["engine"])
            if "ollama_model" in polish and "ollama_model" not in data:
                cfg["ollama_model"] = polish["ollama_model"]
            if "categories" in polish and "polish_categories" not in data:
                cfg["polish_categories"] = polish["categories"]
        output = data.get("output") or {}
        if isinstance(output, dict):
            for key in ("copy_to_clipboard", "paste_to_active_app", "submit_to_active_app"):
                if key in output and key not in data:
                    cfg[key] = output[key]
        for key, value in data.items():
            if key in (
                "azure",
                "app",
                "speech",
                "local_model",
                "model_download",
                "ollama",
                "output",
                "faster_whisper",
                "faster_whisper_model",
            ) or key.startswith("_"):
                continue
            if key == "mlx_model" and isinstance(value, dict):
                continue
            if key == "polish" and isinstance(value, dict):
                continue
            cfg[key] = _normalize_polish_engine(value) if key == "polish_engine" else value
        cfg["azure"] = azure
        cfg["_source"] = str(path)
        break

    # Ensure categories are always populated so the settings UI and app→mode
    # mapping have data to work with, even for a config that predates this key.
    cats = cfg.get("polish_categories")
    if not isinstance(cats, list) or not cats:
        cfg["polish_categories"] = default_polish_categories()

    _CACHE = cfg
    return cfg


def ensure_polish_categories_persisted() -> None:
    """Write the built-in polish categories into config.json if the file lacks
    them, so defaults are physically present and editable by the user."""
    path = config_path_for_write()
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    cats = data.get("polish_categories")
    if isinstance(cats, list) and cats:
        return
    data["polish_categories"] = default_polish_categories()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_config(reload=True)


def get_azure_config() -> dict[str, Any]:
    return load_config()["azure"]


def config_path_for_write() -> Path:
    """Return the config file to write to: the first existing candidate, or the
    user profile config if none exists yet.

    Packaged apps must never write mutable settings into the app bundle; using
    the home config path as the fallback is also safe for source checkouts.
    """
    for path in _candidate_paths():
        if path.is_file():
            return path
    return Path.home() / ".copilot-voice-shell" / "config.json"


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
