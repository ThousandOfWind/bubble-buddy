"""GitHub Copilot account login and text-only polishing, following pi.dev.

GitHub's device grant yields a durable GitHub token, which mints short-lived
Copilot tokens. These are distinct credentials, not a generic OAuth refresh
-token exchange. Never read pi/gh/Copilot CLI credentials or enable model policy.
See docs/copilot.md for protocol sources, usage limits, and supported scope.
"""
from __future__ import annotations

import atexit
import hashlib
import math
import re
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable

from .credential_store import load_credentials, locked_store, save_credentials

CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEVICE_URL = "https://github.com/login/device/code"
OAUTH_URL = "https://github.com/login/oauth/access_token"
TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
VERIFICATION_URL = "https://github.com/login/device"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_MAX_OUTPUT_TOKENS = 2048
# Verified in pi's Copilot catalog; used only if the server omits endpoint metadata.
_KNOWN_ENDPOINTS = {
    "gpt-5.6-luna": "/responses",
    "gpt-5-mini": "/responses",
    "gpt-5.4-mini": "/responses",
    "gpt-4.1": "/chat/completions",  # retain explicit legacy choices, not the default
}
_LOW_LATENCY_PROFILES = {"gpt-5.6-luna", "gpt-5-mini", "gpt-5.4-mini"}
# Compatibility headers used by pi's Copilot provider.
_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
    "Accept": "application/json",
}
_API_VERSION = "2026-06-01"
_AUTH_PATH = Path.home() / ".bubble-buddy" / "copilot-auth.bin"
_LOCK = threading.Lock()
_REFRESH_MARGIN = 300
_MODEL_CACHE_TTL = 60
_MODEL_CACHE_LOCK = threading.Lock()
_MODEL_CACHE: tuple[str, float, dict[str, str]] | None = None
_RESPONSE_CLIENT_LOCK = threading.Lock()
_RESPONSE_CLIENTS: dict[str, Any] = {}


class AuthRequiredError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "GitHub Copilot sign-in required. Use the sign-in button or "
            "`bubble-buddy auth login copilot`."
        )


def _store():
    return locked_store(_AUTH_PATH, _LOCK)


def _load(store: Any) -> dict[str, Any]:
    return load_credentials(store, "GitHub Copilot")


def _save(store: Any, value: dict[str, Any]) -> None:
    save_credentials(store, value, "GitHub Copilot")


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _request(method: str, url: str, **kwargs: Any):
    import httpx

    try:
        # Never forward either token to redirect targets.
        return httpx.request(method, url, follow_redirects=False, timeout=30, **kwargs)
    except httpx.HTTPError:
        raise RuntimeError("GitHub Copilot network error. Try again later; saved credentials are unchanged.") from None


def _check_status(status: int) -> None:
    if status == 401:
        raise AuthRequiredError()
    if status in (403, 404):
        raise RuntimeError(
            f"GitHub Copilot access unavailable (HTTP {status}). "
            "Check your Copilot plan, organization policy and model access. Repeated login may not help."
        )
    if status == 429:
        raise RuntimeError("GitHub Copilot quota/rate limit reached. Try later or select a local polish engine.")
    if not 200 <= status < 300:
        raise RuntimeError(f"GitHub Copilot request failed (HTTP {status}).")


def _json(response: Any) -> dict[str, Any]:
    _check_status(response.status_code)
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except ValueError:
        raise RuntimeError("Invalid GitHub Copilot response.") from None


def _api_base(access: str) -> str:
    # pi derives the account-specific endpoint from the authenticated token.
    # Constrain it to known GitHub-owned hosts, never arbitrary URLs from config.
    hosts = re.findall(r"(?:^|;)proxy-ep=([^;]*)", access)
    if not hosts:
        return "https://api.individual.githubcopilot.com"
    if len(hosts) != 1 or not re.fullmatch(r"proxy\.(?:(?:individual|business|enterprise)\.)?githubcopilot\.com", hosts[0]):
        raise RuntimeError("Untrusted Copilot API endpoint in token response.")
    return "https://" + hosts[0].replace("proxy.", "api.", 1)


def _exchange(github_token: str) -> dict[str, Any]:
    data = _json(_request("GET", TOKEN_URL, headers={**_HEADERS, "Authorization": f"Bearer {github_token}"}))
    access, expires = data.get("token"), data.get("expires_at")
    if not isinstance(access, str) or not access or not _number(expires) or expires <= time.time():
        raise RuntimeError("Invalid Copilot token response.")
    _api_base(access)
    return {"github_token": github_token, "access": access, "expires": expires}


def _credentials(*, rejected_access: str = "") -> dict[str, Any]:
    with _store() as store:
        value = _load(store)
        github = value.get("github_token")
        if not isinstance(github, str) or not github or value.get("reauth_required"):
            raise AuthRequiredError()
        access, expires = value.get("access"), value.get("expires", 0)
        if (not isinstance(access, str) or not access or not _number(expires)
                or expires - time.time() < _REFRESH_MARGIN
                or (rejected_access and rejected_access == access)):
            try:
                value = _exchange(github)
            except AuthRequiredError:
                _save(store, {})  # GitHub token itself was revoked
                raise
            _save(store, value)
        _api_base(value["access"])
        return value


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise RuntimeError("GitHub Copilot sign-in cancelled.")


def _wait(seconds: float, deadline: float, cancelled: Callable[[], bool] | None) -> None:
    until = min(time.monotonic() + seconds, deadline)
    while time.monotonic() < until:
        _check_cancelled(cancelled)
        time.sleep(min(0.2, max(0, until - time.monotonic())))
    _check_cancelled(cancelled)


def sign_in(
    *,
    on_code: Callable[[dict[str, Any]], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    timeout: float = 900,
) -> dict[str, Any]:
    """Explicit, bounded device login. Only this function opens a browser."""
    _check_cancelled(cancelled)
    if not _number(timeout):
        raise ValueError("GitHub login timeout must be a positive finite number.")
    with _store():
        pass  # fail before asking for authorization if protected storage is unavailable
    device = _json(_request("POST", DEVICE_URL, headers=_HEADERS,
                            data={"client_id": CLIENT_ID, "scope": "read:user"}))
    code, user_code = device.get("device_code"), device.get("user_code")
    interval, expires = device.get("interval", 5), device.get("expires_in")
    if (not isinstance(code, str) or not code or not isinstance(user_code, str)
            or not re.fullmatch(r"[A-Z0-9-]{4,32}", user_code)
            or not _number(interval) or not _number(expires)):
        raise RuntimeError("Invalid GitHub device-code response.")
    if device.get("verification_uri") != VERIFICATION_URL:
        raise RuntimeError("Untrusted GitHub verification URL.")
    _check_cancelled(cancelled)
    deadline = time.monotonic() + min(expires, timeout, 900)
    notification = {"user_code": user_code, "verification_uri": VERIFICATION_URL, "expires_in": min(expires, timeout, 900)}
    if on_code:
        on_code(notification)
    else:
        from .diagnostics import print_console_only

        print_console_only(f"Open {VERIFICATION_URL} and enter code: {user_code}")
    _check_cancelled(cancelled)
    try:
        webbrowser.open(VERIFICATION_URL)
    except Exception:
        pass  # the visible URL/code also works with a manually opened browser
    interval = max(1, interval)
    while time.monotonic() < deadline:
        # RFC 8628: wait even before the first poll; slow_down increases by >=5s.
        _wait(interval, deadline, cancelled)
        if time.monotonic() >= deadline:
            break
        data = _json(_request("POST", OAUTH_URL, headers=_HEADERS, data={
            "client_id": CLIENT_ID, "device_code": code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }))
        _check_cancelled(cancelled)
        github = data.get("access_token")
        if isinstance(github, str) and github:
            value = _exchange(github)
            _check_cancelled(cancelled)
            with _store() as store:
                _save(store, value)
            _clear_model_cache()
            return {"signed_in": True, "method": "device_code", "account": ""}
        error = data.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            required = data.get("interval")
            interval = max(interval + 5, required if _number(required) else 0)
            continue
        if error == "access_denied":
            raise RuntimeError("GitHub Copilot sign-in was declined.")
        if error == "expired_token":
            break
        raise RuntimeError("GitHub device authorization failed. Start sign-in again.")
    raise RuntimeError("GitHub Copilot sign-in timed out. Start again to get a new code.")


def sign_out() -> None:
    """Forget only Bubble Buddy's Copilot credentials, not server-side grants."""
    with _store() as store:
        _save(store, {})
    _clear_model_cache()


def auth_status() -> dict[str, Any]:
    try:
        _credentials()
        return {"signed_in": True, "method": "device_code", "account": ""}
    except AuthRequiredError:
        return {"signed_in": False, "method": "", "account": ""}


def _headers(access: str) -> dict[str, str]:
    return {
        **_HEADERS, "Authorization": f"Bearer {access}",
        "X-GitHub-Api-Version": _API_VERSION,
        "X-Initiator": "user", "Openai-Intent": "conversation-edits",
    }


def _authorized_action(action: Callable[[str], Any]) -> Any:
    credentials = _credentials()
    for attempt in range(2):
        access = credentials["access"]
        try:
            return action(access)
        except AuthRequiredError:
            if attempt == 0:
                credentials = _credentials(rejected_access=access)
            else:
                with _store() as store:
                    current = _load(store)
                    if current.get("access") == access:
                        _save(store, {**current, "reauth_required": True})
                raise
    raise AuthRequiredError()


def _authorized_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    return _authorized_action(lambda access: _json(
        _request(method, _api_base(access) + path, headers=_headers(access), **kwargs)
    ))


def _clear_model_cache() -> None:
    global _MODEL_CACHE
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE = None


def _model_cache_key(access: str) -> str:
    # No credentials or transcripts in catalog entries. A renewed token/account
    # gets a fresh catalog, and an alternate credential store cannot share it.
    return str(_AUTH_PATH) + ":" + hashlib.sha256(access.encode()).hexdigest()


def _parse_models(data: Any, *, individual: bool) -> dict[str, str]:
    if not isinstance(data, list):
        raise RuntimeError("Invalid Copilot models response.")
    picker, policy_enabled = {}, {}
    for item in data:
        if not isinstance(item, dict):
            continue
        model = item.get("id")
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", model):
            continue
        policy = item.get("policy") or {}
        if not isinstance(policy, dict) or policy.get("state") == "disabled":
            continue
        endpoints = item.get("supported_endpoints")
        if endpoints is None:
            endpoint = _KNOWN_ENDPOINTS.get(model)
        elif isinstance(endpoints, list):
            # These strings choose a protocol, never an arbitrary URL. Prefer
            # Responses so modern reasoning models aren't sent legacy parameters.
            endpoint = next((p for p in ("/responses", "/chat/completions") if p in endpoints), None)
        else:
            endpoint = None
        if not endpoint or (model in _LOW_LATENCY_PROFILES and endpoint != "/responses"):
            continue  # don't silently lose the verified low-reasoning profile on a chat-only route
        if item.get("model_picker_enabled") is True:
            picker[model] = endpoint
        if policy.get("state") == "enabled":
            policy_enabled[model] = endpoint
    # Match pi's policy fallback only for Individual accounts.
    return picker or (policy_enabled if individual else {})


def _model_catalog(*, force: bool = False) -> dict[str, str]:
    global _MODEL_CACHE
    access = _credentials()["access"]  # never let cached metadata bypass auth
    key = _model_cache_key(access)
    with _MODEL_CACHE_LOCK:
        if not force and _MODEL_CACHE and _MODEL_CACHE[0] == key and time.monotonic() - _MODEL_CACHE[1] < _MODEL_CACHE_TTL:
            return dict(_MODEL_CACHE[2])

        def fetch(current_access: str) -> tuple[str, dict[str, str]]:
            base = _api_base(current_access)
            data = _json(_request("GET", base + "/models", headers=_headers(current_access)))
            return _model_cache_key(current_access), _parse_models(
                data.get("data"), individual=base == "https://api.individual.githubcopilot.com",
            )

        current_key, models = _authorized_action(fetch)
        _MODEL_CACHE = (current_key, time.monotonic(), models)
        return dict(models)


def list_models() -> list[str]:
    """Explicit listing always refreshes enabled Responses/chat-completions models."""
    return sorted(_model_catalog(force=True))


def _rewrite_error() -> RuntimeError:
    return RuntimeError("Copilot returned an empty, refused or incomplete rewrite. Original transcript was not rewritten.")


def _response_client(base: str) -> Any:
    import httpx
    from openai import OpenAI

    with _RESPONSE_CLIENT_LOCK:
        if base not in _RESPONSE_CLIENTS:
            # Reuse TCP/TLS connections, not credentials. Each request supplies
            # its own authorization, including after token renewal/account change.
            _RESPONSE_CLIENTS[base] = OpenAI(
                api_key="unused", base_url=base, max_retries=0, timeout=30,
                http_client=httpx.Client(follow_redirects=False, timeout=30),
            )
        return _RESPONSE_CLIENTS[base]


def _close_response_clients() -> None:
    with _RESPONSE_CLIENT_LOCK:
        clients = list(_RESPONSE_CLIENTS.values())
        _RESPONSE_CLIENTS.clear()
    for client in clients:
        client.close()


atexit.register(_close_response_clients)


def _responses_request(access: str, body: dict[str, Any]) -> dict[str, Any]:
    import httpx
    from openai import APIConnectionError, APIStatusError

    try:
        client = _response_client(_api_base(access))
        # Match pi's Responses SSE contract. Only the complete response is
        # delivered; deltas/reasoning must never become a pasted instruction.
        with client.responses.create(**body, extra_headers=_headers(access)) as stream:
            for event in stream:
                if event.type == "response.completed":
                    return event.response.model_dump()
                if event.type in ("error", "response.failed", "response.incomplete"):
                    raise _rewrite_error()
        raise _rewrite_error()  # disconnect/[DONE] without a completed response
    except APIStatusError as exc:
        try:
            _check_status(exc.status_code)  # 401 alone permits retry
        except RuntimeError as error:
            raise error from None  # do not chain the SDK's raw provider body
        raise RuntimeError("GitHub Copilot Responses request failed.") from None
    except (APIConnectionError, httpx.HTTPError):
        raise RuntimeError("GitHub Copilot network error. Original transcript was not rewritten.") from None


def _response_text(result: dict[str, Any]) -> str:
    try:
        if result.get("status") != "completed" or result.get("error") or result.get("incomplete_details"):
            raise ValueError
        parts = []
        for item in result["output"]:
            if item.get("type") == "reasoning":
                continue
            if item.get("type") != "message" or item.get("role") != "assistant" or item.get("status") not in (None, "completed"):
                raise ValueError
            for content in item["content"]:
                if content.get("type") != "output_text" or not isinstance(content.get("text"), str):
                    raise ValueError
                parts.append(content["text"])
        text = "".join(parts).strip()
        if not text:
            raise ValueError
        return text
    except (KeyError, TypeError, ValueError, AttributeError):
        raise _rewrite_error() from None


def polish(text: str, context: str = "", language_preference: str = "", mode_prompt: str = "") -> str:
    if not text.strip():
        return text
    from .config import load_config
    from .polish import build_rewrite_system_prompt, build_rewrite_user_prompt

    cfg = load_config()
    model = str(cfg.get("copilot_model") or DEFAULT_MODEL).strip()
    effort = cfg.get("copilot_reasoning_effort", DEFAULT_REASONING_EFFORT)
    max_tokens = cfg.get("copilot_max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
    if effort not in ("low", "medium", "high"):
        raise ValueError("copilot_reasoning_effort must be low, medium or high.")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 16 <= max_tokens <= 16384:
        raise ValueError("copilot_max_output_tokens must be an integer between 16 and 16384.")
    endpoint = _model_catalog().get(model)
    if not endpoint:
        raise RuntimeError(
            "Selected Copilot model is unavailable or has no supported text endpoint. "
            "Use `bubble-buddy auth models copilot`, set copilot_model, or enable the model in Copilot's own UI. "
            "No automatic model substitution is performed."
        )
    system = build_rewrite_system_prompt(mode_prompt, language_preference)
    user = build_rewrite_user_prompt(text, context)
    try:
        if endpoint == "/responses":
            body: dict[str, Any] = {
                "model": model,
                "input": [{"role": "developer", "content": system}, {"role": "user", "content": user}],
                "stream": True, "store": False, "max_output_tokens": max_tokens,
            }
            if model in _LOW_LATENCY_PROFILES:
                body["reasoning"] = {"effort": effort}
                body["text"] = {"verbosity": "low"}
            return _response_text(_authorized_action(lambda access: _responses_request(access, body)))
        result = _authorized_request("POST", "/chat/completions", json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False, "max_tokens": max_tokens,
        })
        choice = result["choices"][0]
        message = choice["message"]
        output = message["content"]
        if (choice.get("finish_reason") not in (None, "stop") or message.get("refusal")
                or not isinstance(output, str) or not output.strip()):
            raise ValueError
        return output.strip()
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        _clear_model_cache()
        raise _rewrite_error() from None
    except RuntimeError:
        _clear_model_cache()  # recheck policy/catalog next time, not a second paid request now
        raise
