"""Small provider-aware sign-in bridge shared by desktop frontends."""
from __future__ import annotations

from typing import Any


def required_providers(backend: str, polish_engine: str, polish_mode: str | None = None) -> tuple[str, ...]:
    providers = ["codex"] if backend == "codex" else []
    if backend == "azure" or (polish_engine == "azure" and polish_mode != "off"):
        providers.append("azure")
    if polish_engine == "copilot" and polish_mode != "off":
        providers.append("copilot")
    return tuple(providers)


def provider_name(provider: str) -> str:
    return {"codex": "Codex / ChatGPT", "azure": "Azure", "copilot": "GitHub Copilot"}[provider]


def client(provider: str) -> Any:
    if provider == "codex":
        from . import codex_client
        return codex_client
    if provider == "azure":
        from . import azure_client
        return azure_client
    if provider == "copilot":
        from . import copilot_client
        return copilot_client
    raise ValueError(f"Unsupported account provider: {provider}")


def auth_status(providers: tuple[str, ...]) -> dict[str, Any]:
    """Pick the first account needing sign-in, including mixed ASR/polish setups."""
    statuses = []
    for provider in providers:
        try:
            status = client(provider).auth_status()
        except Exception as exc:
            # A network/keychain error isn't proof that the user is logged out.
            status = {"signed_in": None, "error": str(exc)}
        statuses.append({**status, "provider": provider})
    return next((s for s in statuses if s.get("signed_in") is False),
                next((s for s in statuses if s.get("error")),
                     statuses[0] if statuses else {"signed_in": True, "provider": ""}))


def sign_in(provider: str, *, cancelled: Any = None, on_code: Any = None) -> dict[str, Any]:
    kwargs = {"cancelled": cancelled} if provider in ("codex", "copilot") else {}
    if provider == "copilot":
        kwargs["on_code"] = on_code
    return {**client(provider).sign_in(**kwargs), "provider": provider}
