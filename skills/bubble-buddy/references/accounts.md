# Account routes: capabilities, login and verification

First complete the **platform + route intake** in [`install.md`](install.md).
Ask which code agent the user means; do not equate context-plugin support,
installation of a coding agent, or pi's login support with a Bubble Buddy backend.
This reference is bundled with the skill and does not require a source checkout.

## Capability boundary

| Account | Audio → text in Bubble Buddy | Text polish in Bubble Buddy |
| --- | --- | --- |
| GitHub Copilot | **No verified/implemented audio transcription channel**; choose a separate transcriber | `polish_engine: copilot`, verified with real text requests |
| Codex / ChatGPT | `backend: codex`, **experimental, historical-source evidence only; live audio access has not been verified** | No `polish_engine: codex` implementation |
| Claude Code / other code agents | No implemented account backend | No implemented account backend |
| Azure OpenAI | `backend: azure`, resource/deployment access required | `polish_engine: azure`, resource/deployment access required |

**Copilot audio-file E2E means WAV → local Whisper → text → Copilot → output.**
It does not mean Copilot received/recognized the audio. Do not claim every code
agent can replace Azure speech recognition. If the user wants a currently
verified code-agent-only audio route with no local/Azure transcriber, explain
that this is not established; ask whether they accept local recognition or an
explicitly experimental Codex trial instead.

## GitHub Copilot: local speech + account polish

After user confirmation:

- Windows: **Full**, `backend: faster-whisper`.
- Apple Silicon Mac: **Full**, `backend: mlx`.
- Intel Mac: local `faster-whisper` only with a verified compatible app build;
  do not install an ARM-only DMG or select MLX.
- Set `polish: auto`, `polish_engine: copilot`. These are separate from the
  `copilot` *polish category* (the general-purpose category).
- No Azure endpoint or role is needed for this combination. The audio remains
  local, but **recognized text and selected context are sent to Copilot**.
  Copilot plan quotas, model policy and organizational restrictions still apply.
- Default model/profile comes from [`config.schema.json`](config.schema.json):
  `copilot_model`, `copilot_reasoning_effort`, `copilot_max_output_tokens`.
  Currently GPT-5.6 Luna / low / 2048. Preserve explicit model choices and check
  the user's enabled model list instead of silently choosing an expensive fallback.

### Start a real login, then hand off precisely

Run login **on the confirmed target machine**, not a different host where the
assistant happens to run. Verify the installed build offers Copilot first.

**Installed desktop:** save the agreed settings, then use the **Sign in to GitHub
Copilot** banner above the pet. The browser opens `https://github.com/login/device`;
the app displays a fresh device code. In the Qt desktop, the sign-in button can
cancel the flow. The legacy macOS overlay uses **Account**.

**Source checkout with uv:**

```bash
uv run bubble-buddy auth login copilot
uv run bubble-buddy auth status copilot
uv run bubble-buddy auth models copilot
```

When asking the user to authorize, give all of these:

1. The exact verification URL returned by the active flow (currently
   `https://github.com/login/device`).
2. The **freshly issued** user code—never a fabricated code, old log entry or a
   code copied from another app/session. Don't put real codes in skill examples.
3. Its actual expiry and confirmation that the app/poller is still waiting.
4. Ask the user to choose the intended GitHub account, inspect the displayed
   application/permissions, and authorize in the browser. Bubble Buddy follows
   pi's Copilot OAuth client, so the authorization page may name that client
   rather than Bubble Buddy. Do not ask for their password, MFA code or token.
5. Re-check status and the compatible enabled-model list after authorization.
   If expired/cancelled, start a new flow rather than leaving them at a dead page.

If tools cannot start the app, say so and give the concrete button/command on the
user's machine; do not imply a browser is open or authorization is pending.

To forget **only Bubble Buddy's** Copilot credentials, when requested:

```bash
uv run bubble-buddy auth logout copilot
```

Credentials use `~/.bubble-buddy/copilot-auth.bin` with OS protection. Do not open,
copy or expose it. The GitHub credential renews short-lived Copilot tokens; this
reduces repeated login, not account/role/plan restrictions. Logout is local
credential removal, not account-wide grant revocation or subscription cancellation.

## Codex / ChatGPT: experimental audio route

Only offer `backend: codex` after explicit acceptance of these limits:

- Historical Codex source shows a ChatGPT account dictation route; it is not a
  public stable audio API, and **we have not verified live audio access**.
- Batch audio, at most 120 seconds; desktop recording is capped at 119 seconds
  even if the configured limit is 0/unlimited. No realtime preview or model/language
  selection on this route. Login does not prove transcription entitlement.
- Verify the chosen app version supports this backend. Cloud-only use can use a
  compatible lean build; don't select Full unless local recognition is also wanted.
- Ask separately how to polish: off/rules/Ollama, Copilot (a separate account),
  or Azure. Do not silently add an Azure dependency to an "avoid Azure" request.

Desktop: save the chosen backend and click **Sign in to Codex / ChatGPT**.
Source checkout:

```bash
uv run bubble-buddy auth login codex
uv run bubble-buddy auth status codex
```

The browser flow uses `http://localhost:1455/auth/callback`; close conflicting
pi/Codex login flows first. Report the actual browser state; no GitHub device
code is involved here. Only the user authorizes. Credentials are independently
OS-protected at `~/.bubble-buddy/codex-auth.bin`; never import another app's tokens.
An actual public-audio smoke test is required before claiming this account can
transcribe. On 403/404, explain unavailable service access—do not loop login or
claim refresh will grant permission.

## Errors and validation

Identify the **failing component and provider** first. A local audio backend can
still fail in Copilot/Azure polish. Do not send every "401", "login" or "token"
message to Azure troubleshooting. Azure-specific failures use
[`runbooks/auth-failure.md`](runbooks/auth-failure.md).

- Authentication failure: use that provider's app login; refresh is bounded and
  silent. Do not read pi/gh/VS Code/Copilot CLI credential files.
- 403/404: check account entitlement, model/organization policy or experimental
  endpoint availability; repeated login may not help.
- 429: quota/rate limit; do not retry continuously or purchase extra usage.
- Model unavailable: list enabled compatible models, offer choices, get approval.
  Never auto-enable model policies or accept extra billing terms for the user.
- Network/storage failure: preserve existing settings/credentials; don't delete
  auth files as the default fix. A corrupt/unreadable credential record offers
  an explicit repair-login action; transient network uncertainty alone does not
  mean the account is logged out.

Ask before billable calls, microphone capture or submitting into another app.
Report login, model access, speech recognition, polish and delivery separately.
A passing mocked suite is not live E2E; file-based E2E is not microphone or OS
paste coverage. Do not claim a live test for one account/provider proves another.
