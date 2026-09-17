# GitHub Copilot account polishing

Use **local Whisper/MLX for speech recognition** and **GitHub Copilot for text
polishing**. This combination needs no Azure endpoint, role, or API key.
Copilot receives the recognized **text and selected context**, not the audio.
It is not a fully offline workflow, nor a Copilot speech-transcription backend.

## Quick start

Use the **Full** installer for local Whisper/MLX, or run from source. The lean
Azure installer does not bundle local speech recognition libraries.

1. Settings → Transcription: select `faster-whisper` (or `mlx` on Apple Silicon).
   Download/configure a local model if needed.
2. Settings → Polish: select mode `auto`, engine `copilot`, and model
   `gpt-5.6-luna`. Keep reasoning at `low` and the output budget at `2048`.
3. Save, click **Sign in to GitHub Copilot**, and enter the displayed device code
   at `https://github.com/login/device`. The system browser opens automatically;
   if it cannot open, use the displayed URL manually. Do not share the code.
4. Authorize the account that has Copilot access. The app continues automatically.
   The sign-in button can cancel while waiting; a device code expires after at
   most 15 minutes. The legacy macOS overlay shows the code through **Account**.
5. Record a short phrase to verify transcription and polish.

Equivalent config (ordinary settings only; no credentials):

```json
{
  "backend": "faster-whisper",
  "model": "small",
  "polish": "auto",
  "polish_engine": "copilot",
  "copilot_model": "gpt-5.6-luna",
  "copilot_reasoning_effort": "low",
  "copilot_max_output_tokens": 2048
}
```

Grouped config also accepts `polish: {"mode": "auto", "engine": "copilot",
"copilot_model": "gpt-5.6-luna", "copilot_reasoning_effort": "low",
"copilot_max_output_tokens": 2048}`. Top-level values take precedence.
Existing explicit model choices are not overwritten when defaults change.
`polish: "copilot"` is the existing **general-purpose category**, not the
provider; **`polish_engine: "copilot"`** selects the account-based LLM.
`polish: "off"` does not call Copilot or prompt for its account.

CLI:

```bash
uv run bubble-buddy auth login copilot
uv run bubble-buddy auth status copilot
uv run bubble-buddy auth models copilot
uv run bubble-buddy desktop --backend faster-whisper --polish auto --polish-engine copilot
uv run bubble-buddy auth logout copilot
```

For Apple Silicon use `--backend mlx` and configure `mlx_model`. CLI backend and
polish flags apply to that launch; use Settings/config to persist them. Your
existing Azure/Codex configuration is not automatically changed.

## Models and billing

### Default profile: quick, faithful short-text rewriting

[GitHub's official model comparison](https://docs.github.com/en/copilot/reference/ai-models/model-comparison)
recommends **GPT-5.6 Luna** for fast help with simple/repetitive tasks and describes
it as lightweight and cost-efficient. That matches dictation cleanup better than
a deep-reasoning coding agent. GitHub now lists GPT-4.1 as a
[utility model](https://docs.github.com/en/copilot/reference/ai-models/supported-models#utility-models),
so it is no longer our default.

| Setting | Default | Rationale |
| --- | --- | --- |
| Model | `gpt-5.6-luna` | Officially recommended for small, fast edits |
| API | Responses, streaming | Matches pi's Copilot model contract |
| Reasoning | `low` | Low-latency quality/speed tradeoff; avoid medium/high reasoning for simple cleanup |
| Text verbosity | `low` | Return only the faithful rewrite, not explanations or a summary |
| Output budget | `2048` | Ceiling includes reasoning + final text; not a target output length |
| Response storage | `false` | No server response-history continuation is requested |
| Tools / temperature / paid priority tier | Not sent | No agent loop, unsupported temperature knob, or implicit priority purchase |

The [OpenAI Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
describes the model as fast and cost-sensitive. It supports `none` on the public
OpenAI API, but pi's Copilot profile does not map an off/none level; we use its
verified `low` setting rather than assume public-API settings work unchanged in
Copilot. These are **Bubble Buddy's task-specific defaults**, not a claim that
GitHub published a dictation benchmark or this exact recipe.

In Settings/config, reasoning can be `low`, `medium`, or `high`; output budget
can be 16–16384. Increase the budget for long text if the response is incomplete;
it is never silently truncated and pasted. The low-reasoning/low-verbosity profile
is applied to the verified GPT-5.6 Luna, GPT-5 mini and GPT-5.4 mini Responses
models only, and those profiles require a Responses route from the catalog (or
pi's verified fallback when metadata is absent). We do not silently send them to
a chat-only route without their tuning. Unknown models get no guessed
reasoning/verbosity parameters.

### Availability and request latency

- `auth models copilot` lists currently enabled, compatible text models and
  explicitly refreshes the catalog. Set `copilot_model` to one of those IDs.
  Account/organization policy still determines availability. If Luna is not
  available, select an enabled alternative yourself; no silent model substitution.
- Responses and chat-completions endpoints are routed separately. Anthropic-only
  endpoints are not supported. If endpoint metadata is missing, only the four
  verified entries in `_KNOWN_ENDPOINTS` are assumed (including GPT-4.1 for an
  explicitly pinned, still-authorized legacy configuration).
- Following the [latency optimization guide](https://developers.openai.com/api/docs/guides/latency-optimization),
  normal polishing caches the enabled-model catalog for **60 seconds** instead
  of fetching it before every phrase. The cache is scoped to the credential store
  and current token; login, logout, token changes and inference failures invalidate
  it. There is no stale-on-error fallback; server-side policy remains authoritative.
- Model metadata, chat-completions and Responses share a bounded HTTP connection
  pool for each validated Copilot API host. Authorization is supplied per request,
  never cached in client headers. Keepalive is 120 seconds (the server may close
  an idle connection sooner), so speaking for more than five seconds does not
  automatically expire the client's warmed connection. OAuth stays separate.
- When Copilot polishing is enabled, Qt prepares credentials, the model catalog
  and the SDK in a background worker at startup, after settings/login changes,
  and at recording start/stop. The stop-time preparation overlaps final ASR.
  **Preparation never sends text/audio/context, calls model inference, opens a
  login browser, activates a model, or accepts additional-usage terms.** It uses
  the same 60-second catalog policy, without extending stale data or periodic
  idle polling. Only one preparation worker can run; failures are nonfatal and
  shutdown drains it. Disabled polishing does not schedule preparation.
- Raw ASR appears in the overlay before cloud polishing completes. Streaming
  deltas/reasoning are **not** pasted: only the completed, validated rewrite is
  delivered. Metadata preparation cannot eliminate server/network latency or
  guarantee a particular completion time. `[timing]` log entries distinguish
  final ASR, polish, preparation, and local preview durations; they do not log
  transcript/context contents or credentials.
- Like pi, a policy-enabled fallback is allowed for Individual accounts when
  picker flags are absent/false; organization accounts keep picker semantics.
- Bubble Buddy **does not enable model policies or accept extra-usage terms**.
  If a model needs activation, do that in Copilot's own UI, or ask your admin.
- Copilot subscription quotas, premium requests, organization policy and terms
  apply. This is not unlimited/free inference, and OAuth does not bypass access
  restrictions. Each successful polish submits one text inference request; a
  rejected authentication request may be retried once.

Only `github.com` device login is implemented in this slice. Individual and
Business/Enterprise accounts on github.com can use their token-provided standard
GitHub Copilot API host. Custom GitHub Enterprise Server domains are not supported.

## Authentication and long-term use

The implementation follows [pi.dev](https://pi.dev/) and its
[Copilot OAuth source](https://github.com/earendil-works/pi/blob/main/packages/ai/src/auth/oauth/github-copilot.ts),
[device-code poller](https://github.com/earendil-works/pi/blob/main/packages/ai/src/auth/oauth/device-code.ts),
[request headers](https://github.com/earendil-works/pi/blob/main/packages/ai/src/api/github-copilot-headers.ts),
and [provider catalog](https://github.com/earendil-works/pi/blob/main/packages/ai/src/providers/github-copilot.models.ts).
See also GitHub's [device flow documentation](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow).

1. Device authorization requests `read:user` with the public OAuth client ID used
   by pi. Polling waits before the first request and honors `authorization_pending`,
   `slow_down`, denial and expiry. Only explicit login opens a browser.
2. The GitHub OAuth token is exchanged at
   `https://api.github.com/copilot_internal/v2/token` for a **short-lived Copilot
   access token**. This is not an OAuth `refresh_token` grant: the two tokens have
   different roles.
3. Tokens are stored independently at `~/.bubble-buddy/copilot-auth.bin` using
   Windows DPAPI / macOS Keychain / Linux Secret Service. Windows file writes are
   atomic; the Unix file is a keychain synchronization marker. No plaintext
   fallback. Thread/process locks serialize renewal across concurrent recordings.
4. The short-lived token is renewed on demand within 5 minutes of expiry.
   A 401 causes one renewal/retry; repeated failure prompts for login. Revoked
   GitHub credentials are forgotten. Network/429/5xx and access-policy errors do
   not silently erase valid credentials or cause a retry storm.
5. The API endpoint derived from the token is restricted to standard
   `githubcopilot.com` hosts. Credentials are not sent through HTTP redirects.

No pi, GitHub CLI, VS Code or Copilot CLI credentials are read or modified.
`auth logout copilot` forgets only this app's stored tokens. It does not revoke
server-side grants, sign other apps out, or cancel a subscription. For revocation,
use GitHub's authorized OAuth applications/account security settings.

Automatic renewal reduces repeated login but cannot prevent account revocation,
SSO requirements, quota exhaustion or organization policy changes. These Copilot
integration endpoints are used by pi, not a promise of a stable general-purpose
public API. A real account smoke test is still needed to verify your eligibility.

## Failures and validation

Polish errors are reported rather than silently uploading text to Azure or a
second provider. The Qt/native hotkey frontend keeps the recognized text visible
before cloud polish starts, so it remains available for copying if login/model
access/network fails. Hotkey CLI failures also print the original and honor an
explicit `--save-text` path, but never automatically copy, paste or submit the
unpolished fallback; the failure is still surfaced. If a newer recording starts
before an older job completes, the older result is kept in history for manual
copy, not automatically delivered and not shown as the current recording's result.
Closing stops capture and periodic probes, then drains running Qt workers (including
uncancellable Azure login calls) before teardown. Relaunch requires idle workers.
Incomplete, refused or empty model output is not delivered
as a successful rewrite. The existing question/action-intent safeguards also
apply to Copilot output. Transient status errors do not claim the account is
logged out. Corrupt/unreadable saved credentials expose an explicit repair-login
action without automatically opening a browser or deleting the old store.

`tests/test_copilot_auth.py` tests the device flow, refresh/cache/concurrency,
credential isolation, endpoint checks, model policies, text-only request shape,
error handling, CLI/settings and Qt worker signals. It also exercises the real
OpenAI SDK SSE parser over a mock HTTP transport, Responses completion/refusal/
truncation, per-request authentication on reused connections, and catalog-cache
expiry. Provider HTTP calls are mocked; Windows encryption uses dummy credentials
in temporary storage.
Tests do not establish live account entitlement, current quotas or model quality.
For real audio-file -> local ASR -> Copilot -> file-delivery checks, use the
[two-fixture opt-in E2E runner](audio-e2e.md); it is separate from the mocked unit tests.
