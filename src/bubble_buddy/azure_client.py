"""Azure OpenAI integration for transcription and text polishing.

Authentication defaults to the signed-in Azure user credential using an AAD
bearer token, so no API key is stored. Sign-in is silent whenever possible:

1. a persisted browser sign-in (survives restarts via an encrypted token cache),
2. an existing ``az login`` / environment / managed-identity credential,
3. an interactive browser sign-in (no Azure CLI required) triggered on demand.

The interactive browser is only ever opened through :func:`sign_in`; the hot
recording path and background refresh never pop a window unexpectedly. Set
``azure.auth`` to "api_key" in config.json to use a key from an env var instead.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .config import get_azure_config

_client: Any = None
_client_key: tuple[str, str, str] | None = None
_token_provider: Any = None

# Shared AAD credential + cached bearer token. Reusing one credential across all
# clients (batch/stream/realtime) lets the token be cached and refreshed in one
# place instead of re-authenticating on every recording. The token is refreshed
# proactively once it is within _TOKEN_REFRESH_MARGIN seconds of expiring.
_credential_lock = threading.Lock()
_interactive_cred: Any = None  # InteractiveBrowserCredential (persistent cache)
_default_creds: Any = None  # ordered fallback credentials (az login / env / managed id)
_cached_token: Any = None  # azure.core.credentials.AccessToken
_token_lock = threading.Lock()
_TOKEN_REFRESH_MARGIN = 300  # refresh when < 5 min of validity remains
_last_method: str = ""  # which credential last minted the cached token
_account_hint: str = ""  # username from the persisted sign-in, for the UI

_AUTH_DIR = Path.home() / ".bubble-buddy"
_AUTH_RECORD_PATH = _AUTH_DIR / "auth_record.json"
_TOKEN_CACHE_NAME = "bubble-buddy"


class AuthRequiredError(Exception):
    """Raised when no cached/silent credential is available and the caller did
    not permit an interactive sign-in. The overlay catches this to surface a
    friendly 'sign in' prompt instead of a raw stack trace."""


def _tenant_id() -> str:
    """The AAD tenant that owns the Azure OpenAI resource. Required when the
    signed-in user's home tenant differs from the resource tenant, otherwise the
    token is minted for the wrong tenant and the resource rejects it (HTTP 400
    'Token tenant ... does not match resource tenant')."""
    try:
        return str(get_azure_config().get("tenant_id") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _load_auth_record() -> Any:
    global _account_hint
    try:
        from azure.identity import AuthenticationRecord

        if _AUTH_RECORD_PATH.exists():
            record = AuthenticationRecord.deserialize(_AUTH_RECORD_PATH.read_text("utf-8"))
            _account_hint = getattr(record, "username", "") or _account_hint
            # Ignore a record persisted for a different tenant than the one now
            # configured, so switching tenants forces a fresh sign-in instead of
            # silently reusing a wrong-tenant token.
            tenant = _tenant_id()
            if tenant and getattr(record, "tenant_id", "") and record.tenant_id != tenant:
                return None
            return record
    except Exception:  # noqa: BLE001
        pass
    return None


def _save_auth_record(record: Any) -> None:
    global _account_hint
    try:
        _AUTH_DIR.mkdir(parents=True, exist_ok=True)
        _AUTH_RECORD_PATH.write_text(record.serialize(), encoding="utf-8")
        _account_hint = getattr(record, "username", "") or _account_hint
    except Exception:  # noqa: BLE001
        pass


def _get_interactive_credential() -> Any:
    """Browser credential backed by an on-disk (OS-encrypted) token cache so a
    single sign-in survives app restarts. ``disable_automatic_authentication``
    keeps it silent: it returns a cached token or raises rather than opening a
    browser, so only :func:`sign_in` ever prompts."""
    global _interactive_cred
    with _credential_lock:
        if _interactive_cred is None:
            from azure.identity import (
                InteractiveBrowserCredential,
                TokenCachePersistenceOptions,
            )

            tenant = _tenant_id()
            kwargs: dict[str, Any] = {}
            if tenant:
                kwargs["tenant_id"] = tenant
            _interactive_cred = InteractiveBrowserCredential(
                cache_persistence_options=TokenCachePersistenceOptions(
                    name=_TOKEN_CACHE_NAME, allow_unencrypted_storage=True
                ),
                authentication_record=_load_auth_record(),
                disable_automatic_authentication=True,
                **kwargs,
            )
        return _interactive_cred


def _default_credential_list() -> list[Any]:
    """Ordered fallback credentials tried after the persisted browser sign-in.

    When ``azure.tenant_id`` is set we return the *individual* tenant-aware
    credentials (rather than a ChainedTokenCredential) so ``_acquire_token`` can
    validate each token's tenant and move on to the next credential when one
    returns a wrong-tenant token -- a chain would stop at its first success and
    never reach the tenant-steered CLI credentials. Tenant-steered credentials
    come first, then environment. Without a configured tenant we fall back to
    DefaultAzureCredential."""
    global _default_creds
    with _credential_lock:
        if _default_creds is None:
            tenant = _tenant_id()
            creds: list[Any] = []
            if tenant:
                # DefaultAzureCredential does NOT forward a tenant to its Azure CLI
                # / Developer CLI sub-credentials, so a user who ran `az login`
                # against a different (e.g. home) tenant would get a token minted
                # for the wrong tenant and the resource rejects it with HTTP 400
                # 'Token tenant ... does not match resource tenant'. Steer every
                # tenant-aware source at the configured resource tenant, and try
                # them before the (non-steerable) environment/managed-identity
                # sources.
                try:
                    from azure.identity import AzureCliCredential

                    creds.append(AzureCliCredential(tenant_id=tenant))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from azure.identity import AzureDeveloperCliCredential

                    creds.append(AzureDeveloperCliCredential(tenant_id=tenant))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from azure.identity import ManagedIdentityCredential

                    creds.append(ManagedIdentityCredential())
                except Exception:  # noqa: BLE001
                    pass
                try:
                    from azure.identity import EnvironmentCredential

                    creds.append(EnvironmentCredential())
                except Exception:  # noqa: BLE001
                    pass
            else:
                from azure.identity import DefaultAzureCredential

                creds.append(
                    DefaultAzureCredential(exclude_interactive_browser_credential=True)
                )
            _default_creds = creds
        return _default_creds


def _jwt_tenant(token: str) -> str:
    """Best-effort extraction of the ``tid`` (tenant) claim from a JWT access
    token, so we can detect a token minted for the wrong tenant before the
    resource rejects it. Returns "" if it can't be parsed."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # restore base64 padding
        claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        return str(claims.get("tid") or "")
    except Exception:  # noqa: BLE001
        return ""


def _token_matches_tenant(token: Any) -> bool:
    """True unless the token was clearly minted for a different tenant than the
    configured resource tenant. Unknown/unparseable tenants pass (fail open) so
    we never reject a valid token just because it lacks a ``tid`` claim."""
    tenant = _tenant_id()
    if not tenant:
        return True
    tid = _jwt_tenant(getattr(token, "token", "") or "")
    if tid and tid.lower() != tenant.lower():
        print(
            f"[azure] ignoring token minted for tenant {tid} (configured resource "
            f"tenant is {tenant}); will try the next credential / sign-in.",
            flush=True,
        )
        return False
    return True


def _acquire_token(scope: str, *, allow_interactive: bool) -> Any:
    """Try every silent source in turn; only open a browser when explicitly
    permitted. Returns an AccessToken and records which method succeeded. A token
    minted for a different tenant than the configured resource tenant is rejected
    so we surface a proper sign-in instead of a later HTTP 400 from the resource."""
    global _last_method
    # 1) persisted browser sign-in (silent — cached token, no prompt)
    try:
        token = _get_interactive_credential().get_token(scope)
        if _token_matches_tenant(token):
            _last_method = "browser"
            return token
    except Exception:  # noqa: BLE001  (AuthenticationRequiredError / unavailable)
        pass
    # 2) az login / environment / managed identity — try each individually and
    #    skip any that returns a token for the wrong tenant so a non-steerable
    #    source can't shadow the tenant-steered CLI credentials.
    for cred in _default_credential_list():
        try:
            token = cred.get_token(scope)
        except Exception:  # noqa: BLE001
            continue
        if _token_matches_tenant(token):
            _last_method = "cli"
            return token
    # 3) interactive browser sign-in (opt-in only)
    if allow_interactive:
        return _interactive_sign_in(scope)
    raise AuthRequiredError(
        "尚未登录 Azure。点击悬浮窗的『登录 Azure』按钮，或运行 `az login`。"
    )


def _interactive_sign_in(scope: str) -> Any:
    """Open the system browser for a one-time sign-in and persist the result so
    future launches are silent."""
    global _last_method
    cred = _get_interactive_credential()
    record = cred.authenticate(scopes=[scope])
    _save_auth_record(record)
    token = cred.get_token(scope)
    _last_method = "browser"
    return token


def _aad_token(scope: str, *, force: bool = False, allow_interactive: bool = False) -> str:
    """Return a valid AAD bearer token, refreshing it in the background before it
    expires so interactive recordings never block on a fresh login round-trip."""
    global _cached_token
    with _token_lock:
        now = time.time()
        stale = (
            _cached_token is None
            or force
            or (getattr(_cached_token, "expires_on", 0) - now) < _TOKEN_REFRESH_MARGIN
        )
        if stale:
            _cached_token = _acquire_token(scope, allow_interactive=allow_interactive)
        return _cached_token.token


def _default_scope(cfg: dict[str, Any]) -> str:
    return cfg.get("scope", "https://cognitiveservices.azure.com/.default")


def refresh_token(*, force: bool = False) -> None:
    """Proactively refresh the cached AAD token when AAD auth is in use. Safe to
    call from a background timer/thread; never opens a browser; errors swallowed."""
    try:
        cfg = get_azure_config()
        if str(cfg.get("auth", "aad")).strip().lower() != "aad":
            return
        _aad_token(_default_scope(cfg), force=force)
    except Exception:  # noqa: BLE001
        pass


def sign_in() -> dict[str, Any]:
    """Interactively sign in to Azure (opens the system browser) and persist the
    session. Blocking — call from a worker thread. Returns :func:`auth_status`."""
    cfg = get_azure_config()
    if str(cfg.get("auth", "aad")).strip().lower() == "api_key":
        return auth_status()
    scope = _default_scope(cfg)
    token = _interactive_sign_in(scope)
    with _token_lock:
        global _cached_token
        _cached_token = token
    return auth_status()


def auth_status() -> dict[str, Any]:
    """Report the current auth state for the UI without opening a browser.

    Returns a dict: ``signed_in`` (bool), ``method`` (browser/cli/api_key/""),
    ``account`` (username or ""). Cheap after the first call (token is cached)."""
    cfg = get_azure_config()
    if str(cfg.get("auth", "aad")).strip().lower() == "api_key":
        key = str(cfg.get("api_key") or "").strip() or os.environ.get(
            cfg.get("api_key_env", "AZURE_OPENAI_API_KEY"), ""
        )
        return {"signed_in": bool(key), "method": "api_key", "account": ""}
    try:
        _aad_token(_default_scope(cfg))  # silent only
        return {"signed_in": True, "method": _last_method or "browser", "account": _account_hint}
    except Exception:  # noqa: BLE001
        return {"signed_in": False, "method": "", "account": _account_hint}


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

    global _token_provider
    scope = _default_scope(cfg)
    # Reuse the shared auto-refreshing credential/token cache.
    token_provider = lambda: _aad_token(scope)  # noqa: E731
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
        tmp = _Path(tempfile.gettempdir()) / "bubble-buddy-warmup.wav"
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

    scope = _default_scope(cfg)
    provider = lambda: _aad_token(scope)  # noqa: E731
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


def polish(text: str, context: str = "", language_preference: str = "", mode_prompt: str = "") -> str:
    if not text.strip():
        return text
    client, cfg = get_client()
    if mode_prompt.strip():
        # Scenario-specific prompt (per active-app category, possibly user-customized)
        # replaces the generic system prompt so its rules (e.g. browser = no
        # punctuation) aren't contradicted by the default instructions.
        system = mode_prompt.strip()
    else:
        system = POLISH_SYSTEM_PROMPT
        instruction = LANG_POLISH_INSTRUCTIONS.get((language_preference or "").strip().lower())
        if instruction:
            system += f"\n{instruction}"
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
