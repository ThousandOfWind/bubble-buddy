# Codex / ChatGPT account dictation (experimental)

Bubble Buddy can use a **separate ChatGPT/Codex account login** for transcription,
without an Azure endpoint, Azure resource role, or API key. Set `backend` to
`codex`. This is an opt-in experiment, **not a guaranteed long-term replacement
for a public speech API**. Existing backends and defaults are unchanged.

## Setup

1. In desktop Settings → Transcription, select `codex` and save.
2. Click **Sign in to Codex / ChatGPT** and finish login in the system browser.
   The browser callback uses `http://localhost:1455/auth/callback`; close any
   concurrent pi/Codex login using that port first. Login times out after 3 minutes.
3. Record a short clip and check the result. Login success only proves OAuth
   authentication, **not access to the dictation service**.

To remove the Azure dependency completely, also select `rules`, `ollama`, or
[`copilot`](copilot.md) as `polish_engine` (or disable polishing). A `codex` transcription + `azure` polish
configuration still needs Azure permissions; the sign-in banner handles both.

```json
{
  "backend": "codex",
  "polish": "off",
  "polish_engine": "rules",
  "max_record_seconds": 120
}
```

Or use the CLI (installed executables accept the same arguments):

```bash
uv run bubble-buddy auth login codex
uv run bubble-buddy auth status codex
uv run bubble-buddy transcribe recordings/example.wav --backend codex --polish off --plain
uv run bubble-buddy auth logout codex
```

`logout` forgets Bubble Buddy's saved credentials. It does not sign out pi/Codex,
cancel a subscription, or revoke all sessions at the provider. For account-wide
revocation, use the provider's security settings. Re-run `auth login codex` to
change accounts. The legacy macOS native overlay uses its **Account** button.

## What is borrowed from pi.dev

Reference: [pi.dev](https://pi.dev/), its
[provider documentation](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/providers.md),
[Codex OAuth implementation](https://github.com/earendil-works/pi/blob/main/packages/ai/src/auth/oauth/openai-codex.ts),
and [credential storage](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/auth-storage.ts).

- Provider-scoped credentials, separate from ordinary application settings.
- Authorization-code login with PKCE S256, random state, loopback callback, and
  `openid profile email offline_access` scopes.
- Silent refresh of expiring access tokens, saving rotated refresh tokens before
  use. Bubble Buddy refreshes on demand within 5 minutes of expiry.
- Serialized refresh: process/thread locks and re-reading the current credentials
  before rotation prevent concurrent recordings from reusing an old refresh token.
- Explicit sign-in/logout; recording and status checks never launch a browser.

This is a Python implementation of the protocol, not a dependency on the Node
pi runtime. It currently implements browser login only, not pi's device-code or
manual callback fallback. It deliberately **does not import, read, or update**
`~/.pi/agent/auth.json` or `~/.codex/auth.json`: sharing their rotating refresh
tokens risks logging those applications out.

Bubble Buddy uses OS-protected storage at `~/.bubble-buddy/codex-auth.bin`:
Windows DPAPI (atomic file replacement), macOS Keychain, or Linux Secret Service.
On macOS/Linux the file is a synchronization marker; the secret is in the OS
credential service. If protected storage is unavailable, login fails rather than
falling back to plaintext. Tokens and OAuth response bodies are not exposed in
config, CLI status output, or application errors.

## Audio capability is separate from login capability

Pi's subscription login is designed for **LLM inference**. It does not establish
that every supported account provides speech recognition. Bubble Buddy does not
advertise Claude/Copilot account transcription without a verified audio route.

The experimental dictation contract comes from OpenAI's published Codex
[`rust-v0.114.0` voice implementation](https://github.com/openai/codex/blob/rust-v0.114.0/codex-rs/tui/src/voice.rs#L788-L867):

- ChatGPT OAuth → `https://chatgpt.com/backend-api/transcribe`.
- Multipart `file` containing `audio.wav` (`audio/wav`), bearer authorization,
  and `ChatGPT-Account-Id`.
- JSON response with a `text` string.

This is **historical client-source evidence, not a public, stable transcription
API contract**. The corresponding file is no longer at that path on Codex main.
Account/plan/region/service-policy eligibility and endpoint availability must be
checked with an actual user-authorized smoke test. A ChatGPT subscription is not
an OpenAI API credit balance, and no unlimited/free-use claim is made here.

Current Bubble Buddy limits:

- **Batch only**: record first, transcribe after stopping. No realtime previews.
- WAV/FLAC and other formats readable by libsndfile; M4A support is not guaranteed.
  Captured recordings are WAV. Uploaded audio is converted to 24 kHz mono PCM16.
- Non-empty clips of **at most 120 seconds**, input files at most 25 MiB. Longer
  clips are rejected locally, not silently truncated or deleted. Desktop capture
  auto-stops at no more than 119s (including when `max_record_seconds` is 0 or
  larger), leaving 1s of scheduling headroom. Manual CLI recordings/files must
  still be kept within the 120s upload limit.
- No model, language, or prompt selection on this route. Local phrase replacements
  and subsequent text polishing still work. Azure's streaming configuration does
  not affect this backend.
- No automatic switch to a different cloud provider on failure: recordings are
  not silently uploaded to another service and are not deleted by this client.

## Failure handling and long-term use

| Result | Behavior |
| --- | --- |
| Expiring access token | Silent refresh; persist new refresh token |
| HTTP 401 | Refresh once and retry once; repeated failure asks for login |
| Invalid/revoked refresh token | Forget unusable app credentials; ask for login |
| Network failure / HTTP 5xx | Keep credentials; report retryable failure |
| HTTP 403/404 | Explain missing service access/unavailable experimental route; no login loop |
| HTTP 429 | Explain quota/rate limit; no automatic retry storm |

Automatic refresh reduces repeated logins. It cannot override provider
revocation, account limits, tenant policies, or expired **resource permissions**.
The Azure client already caches and refreshes tokens; replacing token handling
alone cannot renew an expired Azure role/PIM assignment. For predictable operation,
keep a supported local Whisper/MLX backend available, or use an authorized public
API with its own billing and credential policy.

## Validation

`tests/test_codex_auth.py` covers PKCE/state checks, mocked browser callback and
code exchange, timeout/cancellation, protected storage, expiry/rotation,
concurrent access, HTTP 401 replay, non-auth errors, audio conversion/limits,
CLI routing, and provider-aware Qt workers. HTTP provider calls are mocked; the
callback test uses only an ephemeral loopback server. Windows DPAPI is tested
with dummy credentials in a temporary directory. No real account login,
subscription entitlement, or live transcription is established by these tests.
