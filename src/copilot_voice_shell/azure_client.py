"""Azure OpenAI integration for transcription and text polishing.

Authentication defaults to the signed-in Azure user credential (via
`az login`) using an AAD bearer token, so no API key is stored. Set
`azure.auth` to "api_key" in config.json to use a key from an env var instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import get_azure_config

_client: Any = None
_client_key: tuple[str, str, str] | None = None
_token_provider: Any = None

POLISH_SYSTEM_PROMPT = (
    "你是语音听写整理器。只输出整理后的用户原始指令，不要解释、不要编号、不要加前缀。\n"
    "任务：修正中英文 ASR 错误、规范技术词、去掉语气词和重复词，整理成更清楚但不改变意图的版本。\n"
    "补全自然的中文/英文标点，尤其句末标点。\n"
    "约束：不要总结成泛泛短句；不要删掉限定条件；不要把命令改成疑问句；不要添加用户没说的新需求；"
    "保留中英混杂表达，不要翻译技术词。"
)

# Per-preference guidance appended to the polish system prompt.
LANG_POLISH_INSTRUCTIONS: dict[str, str] = {
    "zh-en": "语言偏好：保留用户的中英文混杂表达，中文用中文、英文技术词保留英文，不要互相翻译。",
    "zh": "语言偏好：主要用中文表达，但保留专有名词和技术词的英文原文，不要强行翻译成中文。",
    "en": "Language preference: respond in English; keep proper nouns and technical terms as-is.",
}


def transcribe_language_hint(language_preference: str) -> str:
    """Map a language preference to a transcription `language` hint.

    Mixed preferences (e.g. "zh-en") return "" so the model auto-detects, which
    avoids garbling code-switched Chinese/English speech."""
    pref = (language_preference or "").strip().lower()
    if not pref or pref in {"auto", "mixed"} or "-" in pref or "+" in pref:
        return ""
    return pref



def _resolve_api_key(cfg: dict[str, Any]) -> str:
    """Return the Azure API key, preferring the (gitignored) config value
    ``azure.api_key`` and falling back to the env var named by ``api_key_env``.
    Storing the key directly in config.json avoids interactive AAD logins that
    some tenants force to re-authenticate every day via conditional access."""
    key = str(cfg.get("api_key") or "").strip()
    if key:
        return key
    env_name = cfg.get("api_key_env", "AZURE_OPENAI_API_KEY")
    key = os.environ.get(env_name, "")
    if not key:
        raise SystemExit(
            f"Azure API key not set. Put it in 'azure.api_key' in config.json "
            f"or in the '{env_name}' environment variable."
        )
    return key


def _make_client(cfg: dict[str, Any]) -> Any:
    from openai import AzureOpenAI

    endpoint = cfg.get("endpoint")
    if not endpoint:
        raise SystemExit(
            "Azure endpoint not configured. Set 'azure.endpoint' in config.json "
            "(e.g. https://<resource>.cognitiveservices.azure.com/)."
        )
    api_version = cfg.get("api_version", "2025-03-01-preview")
    auth = cfg.get("auth", "aad")

    if auth == "api_key":
        key = _resolve_api_key(cfg)
        return AzureOpenAI(azure_endpoint=endpoint, api_version=api_version, api_key=key)

    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    global _token_provider
    scope = cfg.get("scope", "https://cognitiveservices.azure.com/.default")
    token_provider = get_bearer_token_provider(DefaultAzureCredential(), scope)
    _token_provider = token_provider
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_version=api_version,
        azure_ad_token_provider=token_provider,
    )


def get_client() -> tuple[Any, dict[str, Any]]:
    global _client, _client_key
    cfg = get_azure_config()
    key = (str(cfg.get("endpoint")), str(cfg.get("api_version")), str(cfg.get("auth")))
    if _client is None or _client_key != key:
        _client = _make_client(cfg)
        _client_key = key
    return _client, cfg


def warmup() -> None:
    """Send one tiny transcription request so the first real recording is fast.
    The cold cost is in the first transcription round-trip (connection + endpoint
    warm-up), not just the token. Safe to call in a background thread; errors ignored."""
    try:
        import tempfile
        import wave
        from pathlib import Path as _Path

        get_client()  # build client + acquire token
        tmp = _Path(tempfile.gettempdir()) / "copilot-voice-shell-warmup.wav"
        with wave.open(str(tmp), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 1600)  # 0.1s of silence
        transcribe(tmp, language="")
    except Exception:  # noqa: BLE001
        pass


def transcribe(
    audio_path: Path | str,
    language: str = "",
    prompt: str = "",
    mode: str = "",
    on_delta: Any = None,
) -> str:
    """Transcribe an audio file via Azure OpenAI.

    mode: "batch" (default) waits for the full result; "stream" uses server-sent
    streaming and invokes `on_delta(partial_text)` as text arrives. "realtime"
    also streams (over the Realtime API when available, otherwise falls back to
    response streaming)."""
    client, cfg = get_client()
    mode = (mode or cfg.get("transcribe_mode", "batch") or "batch").strip().lower()
    kwargs: dict[str, Any] = {}
    if language:
        kwargs["language"] = language
    if prompt:
        kwargs["prompt"] = prompt

    if mode == "stream":
        return _transcribe_stream(client, cfg, audio_path, kwargs, on_delta)
    if mode == "realtime":
        return _transcribe_realtime(cfg, audio_path, kwargs, on_delta)

    with open(audio_path, "rb") as handle:
        response = client.audio.transcriptions.create(
            model=cfg["transcribe_deployment"],
            file=handle,
            **kwargs,
        )
    return (getattr(response, "text", "") or "").strip()


def _transcribe_stream(
    client: Any,
    cfg: dict[str, Any],
    audio_path: Path | str,
    kwargs: dict[str, Any],
    on_delta: Any,
) -> str:
    """Stream a transcription response, accumulating text deltas."""
    parts: list[str] = []
    try:
        with open(audio_path, "rb") as handle:
            stream = client.audio.transcriptions.create(
                model=cfg["transcribe_deployment"],
                file=handle,
                stream=True,
                **kwargs,
            )
            for event in stream:
                delta = getattr(event, "delta", None)
                if delta:
                    parts.append(delta)
                    if on_delta is not None:
                        try:
                            on_delta("".join(parts))
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                text = getattr(event, "text", None)
                if text:
                    parts = [text]
    except TypeError:
        # SDK/deployment doesn't support streaming for this model -> fall back.
        with open(audio_path, "rb") as handle:
            response = client.audio.transcriptions.create(
                model=cfg["transcribe_deployment"],
                file=handle,
                **kwargs,
            )
        return (getattr(response, "text", "") or "").strip()
    return "".join(parts).strip()


def _load_pcm24k(audio_path: Path | str) -> bytes:
    """Read a WAV file and return 24 kHz mono 16-bit little-endian PCM,
    the format expected by the Azure realtime audio input buffer."""
    import audioop
    import wave

    with wave.open(str(audio_path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        data = handle.readframes(handle.getnframes())
    if channels == 2:
        data = audioop.tomono(data, width, 0.5, 0.5)
    if width != 2:
        data = audioop.lin2lin(data, width, 2)
    if rate != 24000:
        data, _ = audioop.ratecv(data, 2, 1, rate, 24000, None)
    return data


def _make_realtime_client(cfg: dict[str, Any]) -> Any:
    """Build an AzureOpenAI client pinned to a realtime-capable api-version."""
    from openai import AzureOpenAI

    endpoint = cfg.get("endpoint")
    if not endpoint:
        raise SystemExit("Azure endpoint not configured for realtime transcription.")
    api_version = cfg.get("realtime_api_version", "2025-04-01-preview")
    auth = cfg.get("auth", "aad")
    if auth == "api_key":
        key = _resolve_api_key(cfg)
        return AzureOpenAI(azure_endpoint=endpoint, api_version=api_version, api_key=key)

    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    scope = cfg.get("scope", "https://cognitiveservices.azure.com/.default")
    provider = get_bearer_token_provider(DefaultAzureCredential(), scope)
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_version=api_version,
        azure_ad_token_provider=provider,
    )


def make_realtime_client(cfg: dict[str, Any]) -> Any:
    """Public helper: build a realtime-capable AzureOpenAI client for live streaming."""
    return _make_realtime_client(cfg)


def _transcribe_realtime(
    cfg: dict[str, Any],
    audio_path: Path | str,
    kwargs: dict[str, Any],
    on_delta: Any,
) -> str:
    """Transcribe via the Azure OpenAI Realtime API (WebSocket).

    The recorded WAV is streamed to a realtime transcription session and text
    deltas are collected. Requires the `websockets` package and a realtime-capable
    api-version (see `azure.realtime_api_version`)."""
    import base64

    client = _make_realtime_client(cfg)
    deployment = cfg["transcribe_deployment"]
    pcm = _load_pcm24k(audio_path)
    audio_b64 = base64.b64encode(pcm).decode()

    transcription: dict[str, Any] = {"model": deployment}
    if kwargs.get("language"):
        transcription["language"] = kwargs["language"]
    if kwargs.get("prompt"):
        transcription["prompt"] = kwargs["prompt"]

    parts: list[str] = []
    final = ""
    with client.beta.realtime.connect(
        model=deployment, extra_query={"intent": "transcription"}
    ) as conn:
        conn.send(
            {
                "type": "transcription_session.update",
                "session": {
                    "input_audio_format": "pcm16",
                    "input_audio_transcription": transcription,
                    "turn_detection": None,
                },
            }
        )
        conn.send({"type": "input_audio_buffer.append", "audio": audio_b64})
        conn.send({"type": "input_audio_buffer.commit"})
        for event in conn:
            etype = getattr(event, "type", "")
            if etype == "conversation.item.input_audio_transcription.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    parts.append(delta)
                    if on_delta is not None:
                        try:
                            on_delta("".join(parts))
                        except Exception:  # noqa: BLE001
                            pass
            elif etype == "conversation.item.input_audio_transcription.completed":
                final = getattr(event, "transcript", "") or ""
                break
            elif etype == "error":
                err = getattr(event, "error", event)
                raise RuntimeError(f"Realtime transcription error: {err}")
    return (final or "".join(parts)).strip()


def polish(text: str, context: str = "", glossary: list[str] | None = None, language_preference: str = "") -> str:
    if not text.strip():
        return text
    client, cfg = get_client()
    system = POLISH_SYSTEM_PROMPT
    instruction = LANG_POLISH_INSTRUCTIONS.get((language_preference or "").strip().lower())
    if instruction:
        system += f"\n{instruction}"
    if glossary:
        system += f"\n优先参考这些技术词：{', '.join(glossary)}。"
    user = text
    if context:
        user = f"当前会话摘要：{context}\n\n输入：{text}"
    response = client.chat.completions.create(
        model=cfg["chat_deployment"],
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    result = response.choices[0].message.content or ""
    return result.strip() or text
