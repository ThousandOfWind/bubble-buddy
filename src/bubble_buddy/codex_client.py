"""Experimental ChatGPT/Codex dictation with independent, renewable OAuth.

Protocol references and limitations: docs/codex.md. Never read or mutate pi's or
Codex CLI's credentials: rotating a shared refresh token can log those apps out.
Only sign_in() opens a browser. Tokens stay in OS-protected storage, not config.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
TRANSCRIBE_URL = "https://chatgpt.com/backend-api/transcribe"
REDIRECT_URI = "http://localhost:1455/auth/callback"
_AUTH_PATH = Path.home() / ".bubble-buddy" / "codex-auth.bin"
_LOCK = threading.Lock()
_REFRESH_MARGIN = 300
_MAX_AUDIO_SECONDS = 120
_MAX_FILE_BYTES = 25 * 1024 * 1024


class AuthRequiredError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Codex / ChatGPT sign-in required. Use the sign-in button or "
            "`bubble-buddy auth login codex`."
        )


def _store():
    from .credential_store import locked_store

    return locked_store(_AUTH_PATH, _LOCK)


def _load(store: Any) -> dict[str, Any]:
    from .credential_store import load_credentials

    return load_credentials(store, "Codex")


def _save(store: Any, credentials: dict[str, Any]) -> None:
    from .credential_store import save_credentials

    save_credentials(store, credentials, "Codex")


def _claims(token: str) -> dict[str, Any]:
    # Metadata only; the OAuth server/resource validates the actual token.
    try:
        payload = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _token_request(data: dict[str, str], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    import httpx

    try:
        response = httpx.post(TOKEN_URL, data={"client_id": CLIENT_ID, **data}, timeout=30)
    except httpx.HTTPError:
        raise RuntimeError("Codex authentication network error. Try again later.") from None
    if response.status_code in (400, 401):
        # Do not turn network/rate-limit/server failures into destructive logout.
        try:
            error = response.json().get("error")
            code = error.get("code") if isinstance(error, dict) else error
        except (ValueError, AttributeError):
            code = None
        if code in ("invalid_grant", "refresh_token_expired", "refresh_token_reused", "refresh_token_invalid"):
            raise AuthRequiredError()
    if not response.is_success:
        raise RuntimeError(f"Codex authentication failed (HTTP {response.status_code}). Try signing in again.")
    try:
        result = response.json()
        access = result["access_token"]
        refresh = result.get("refresh_token") or (previous or {}).get("refresh")
        expires_in = result["expires_in"]
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise ValueError
        if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or not math.isfinite(expires_in) or expires_in <= 0:
            raise ValueError
        claims = _claims(access)
        account_id = (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_account_id")
        if not isinstance(account_id, str) or not account_id:
            raise ValueError
        if previous and account_id != previous.get("account_id"):
            raise ValueError
        id_claims = _claims(result.get("id_token", ""))
        email = id_claims.get("email") or (previous or {}).get("account", "")
        return {
            "access": access,
            "refresh": refresh,
            "expires": time.time() + expires_in,
            "account_id": account_id,
            "account": email if isinstance(email, str) else "",
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        # Never include the response body: it contains credentials.
        raise RuntimeError("Invalid Codex OAuth token response.") from None


def _credentials(*, rejected_access: str = "") -> dict[str, Any]:
    from .credential_store import CredentialReadError

    with _store() as store:
        value = _load(store)
        if not value:
            raise AuthRequiredError()
        if any(not isinstance(value.get(key), str) or not value[key] for key in ("refresh", "access", "account_id")):
            raise CredentialReadError("Invalid Codex credential schema. Sign in again to replace it.")
        expires = value.get("expires")
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
            raise CredentialReadError("Invalid Codex credential expiry. Sign in again to replace it.")
        # Re-read under the cross-process lock: another recording may have
        # already rotated the token after the failed request was sent.
        if expires - time.time() < _REFRESH_MARGIN or (rejected_access and value["access"] == rejected_access):
            try:
                value = _token_request(
                    {"grant_type": "refresh_token", "refresh_token": value["refresh"]}, value
                )
            except AuthRequiredError:
                _save(store, {})
                raise
            _save(store, value)
        return value


def _authorization_url() -> tuple[str, str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(32)
    url = AUTH_URL + "?" + urlencode({
        "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email offline_access", "code_challenge": challenge,
        "code_challenge_method": "S256", "state": state,
        "id_token_add_organizations": "true", "codex_cli_simplified_flow": "true",
        "originator": "bubble_buddy",
    })
    return url, verifier, state


def _callback_result(path: str, state: str) -> tuple[int, str, str]:
    url = urlsplit(path)
    params = parse_qs(url.query)
    if url.path != "/auth/callback":
        return 404, "Not found.", ""
    if params.get("state") != [state]:
        return 400, "OAuth state mismatch. Return to the original login window.", ""
    if "error" in params:
        return 200, "Sign-in was declined. You can close this window.", "denied"
    codes = params.get("code", [])
    if len(codes) != 1 or not codes[0] or len(codes[0]) > 8192:
        return 400, "Missing or invalid authorization code.", ""
    return 200, "Authorization received. Return to Bubble Buddy to check sign-in status.", codes[0]


def sign_in(*, timeout: float = 180, cancelled: Any = None) -> dict[str, Any]:
    """PKCE browser login; bounded, blocking, and only explicitly invoked."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Codex login timeout must be a positive finite number.")
    # Fail before opening the browser if secure storage is unavailable.
    with _store():
        pass
    url, verifier, state = _authorization_url()
    result: list[str] = []

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, message, code = _callback_result(self.path, state)
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if code:
                result.append(code)
            try:
                self.wfile.write(message.encode("utf-8"))
            except OSError:
                pass

        def log_message(self, *_args: Any) -> None:
            pass  # callback URLs contain the authorization code

    class Server(HTTPServer):
        def get_request(self):
            sock, address = super().get_request()
            sock.settimeout(2)
            return sock, address

        def handle_error(self, *_args: Any) -> None:
            pass  # no callback request dumps in application logs

    try:
        server = Server(("127.0.0.1", 1455), Callback)
    except OSError:
        raise RuntimeError("Login port 1455 is busy. Finish other Codex/pi logins and retry.") from None
    with server:
        server.timeout = 0.5
        if not webbrowser.open(url):
            raise RuntimeError("Could not open the default browser for Codex login.")
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            if cancelled and cancelled():
                raise RuntimeError("Codex sign-in cancelled.")
            server.handle_request()
    if not result:
        raise RuntimeError("Codex sign-in timed out. Click sign in to retry.")
    if result[0] == "denied":
        raise RuntimeError("Codex sign-in was declined.")
    value = _token_request({
        "grant_type": "authorization_code", "code": result[0],
        "code_verifier": verifier, "redirect_uri": REDIRECT_URI,
    })
    if cancelled and cancelled():
        raise RuntimeError("Codex sign-in cancelled.")
    with _store() as store:
        _save(store, value)
    return {"signed_in": True, "method": "oauth", "account": value["account"]}


def sign_out() -> None:
    """Forget this app's credentials (not global server-side revocation)."""
    with _store() as store:
        _save(store, {})  # also clears OS keychain entries, not just signal files


def auth_status() -> dict[str, Any]:
    """Local sign-in status, not proof of transcription entitlement; no browser."""
    try:
        value = _credentials()
        return {"signed_in": True, "method": "oauth", "account": value.get("account", "")}
    except AuthRequiredError:
        return {"signed_in": False, "method": "", "account": ""}


def _wav_bytes(audio_path: Path | str) -> bytes:
    import numpy as np
    import soundfile as sf

    path = Path(audio_path)
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ValueError("Codex dictation accepts audio files up to 25 MiB.")
    with sf.SoundFile(path) as source:
        if source.frames == 0 or source.frames / source.samplerate > _MAX_AUDIO_SECONDS:
            raise ValueError("Codex dictation requires 0–120 seconds of audio (non-empty).")
        samples = source.read(dtype="float32", always_2d=True).mean(axis=1)
        rate = source.samplerate
    if not np.isfinite(samples).all():
        raise ValueError("Invalid audio samples.")
    # Match Codex's 24 kHz mono PCM16 upload without adding an ML dependency.
    if rate != 24000:
        count = max(1, round(len(samples) * 24000 / rate))
        samples = np.interp(np.arange(count) * rate / 24000, np.arange(len(samples)), samples)
    output = io.BytesIO()
    sf.write(output, samples, 24000, format="WAV", subtype="PCM_16")
    return output.getvalue()


def transcribe(audio_path: Path | str) -> str:
    """Batch dictation only; no model/language/prompt parameters on this route."""
    import httpx

    audio = _wav_bytes(audio_path)
    credentials = _credentials()
    for attempt in range(2):
        try:
            response = httpx.post(
                TRANSCRIBE_URL,
                headers={
                    "Authorization": f"Bearer {credentials['access']}",
                    "ChatGPT-Account-Id": credentials["account_id"],
                    "User-Agent": "bubble-buddy (experimental Codex dictation)",
                },
                files={"file": ("audio.wav", audio, "audio/wav")},
                timeout=120,
            )
        except httpx.HTTPError:
            raise RuntimeError("Codex transcription network error. The recording has not been deleted.") from None
        if response.status_code == 401:
            if attempt == 0:
                credentials = _credentials(rejected_access=credentials["access"])
                continue
            with _store() as store:
                if _load(store).get("access") == credentials["access"]:
                    _save(store, {})
            raise AuthRequiredError()
        if response.status_code in (403, 404):
            raise RuntimeError(
                f"Codex dictation unavailable for this account (HTTP {response.status_code}). "
                "This experimental service is not a public audio API. Use Azure or local Whisper; "
                "signing in again may not grant access."
            )
        if response.status_code == 429:
            raise RuntimeError("Codex dictation quota/rate limit reached. Try later or select another backend.")
        if not response.is_success:
            raise RuntimeError(f"Codex transcription failed (HTTP {response.status_code}).")
        try:
            text = response.json()["text"]
            if not isinstance(text, str):
                raise ValueError
            return text.strip()
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("Invalid Codex transcription response.") from None
    raise AuthRequiredError()
