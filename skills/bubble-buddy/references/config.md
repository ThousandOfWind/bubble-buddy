# Configuring Bubble Buddy

You have a machine-generated schema of every configuration key in
[`config.schema.json`](config.schema.json). Always ground answers in that schema
— never invent keys or defaults.

## Where the config lives

- **Windows:** `%USERPROFILE%\.bubble-buddy\config.json`
- **macOS / Linux:** `~/.bubble-buddy/config.json`

The file is plain JSON. Missing keys fall back to the defaults in the schema, so
a valid config can contain only the keys the user overrode.

## How to use the schema

`config.schema.json` has this shape:

```json
{
  "keys": {
    "backend": { "default": "faster-whisper", "type": "string",
                 "enum": ["faster-whisper","mlx","azure","codex"], "note": "..." },
    "azure.api_key": { "default": "", "type": "string", "secret": true, ... }
  }
}
```

- `enum` — the only valid values. Reject anything else.
- `secret: true` — NEVER print, log, echo or ask the user to paste this value in
  clear text in a shared context. Tell them where to set it, don't handle it.
- Dotted keys like `azure.endpoint` are **nested** in the JSON file:
  ```json
  { "azure": { "endpoint": "https://...", "transcribe_mode": "stream" } }
  ```
- `note` — a short human description carried over from the source comment.

## Workflow when a user wants to change something

1. For a backend/provider change, first complete the platform + Azure/local/code-agent
   intake in [`install.md`](install.md). Reuse confirmed answers; do not infer the
   target OS or cloud consent. Inspect only relevant non-secret config values;
   never dump or ask the user to paste an entire credential-bearing config.
2. **Locate** the relevant key(s) in the schema. Explain default + allowed values.
3. **Validate** the desired value against `type`/`enum`. If invalid, say why and
   list the valid options.
4. **Apply a minimal edit** when file access is available; otherwise show only
   the key(s) that change, correctly nested.
5. **Prefer the Settings UI** for common changes (interface language, backend,
   launch-at-startup) — it validates and applies live. Hand-editing JSON is a
   fallback for advanced keys.

## Common tasks (cheat-sheet)

- **Switch interface language:** `ui_language` (Settings ▸ General ▸ Interface
  language applies it live).
- **Enable start-on-boot:** `launch_at_startup: true` (Settings ▸ General).
- **Pick transcription engine:** `backend` (`mlx` Apple-silicon
  local / `faster-whisper` CPU local / `azure` cloud / `codex` experimental ChatGPT account dictation). A grouped
  `speech: {backend: …}` block is also accepted and migrated. If `azure`, the
  `azure.*` block must be set. `codex` uses its own browser login (`bubble-buddy auth login codex`),
  OS-protected credentials and silent token refresh; batch clips up to 120s only.
  Do not read/copy pi or Codex CLI tokens. Account login does not guarantee audio
  access; see the bundled [account capabilities and limits](accounts.md). To avoid
  Azure entirely, choose off/local polish or explicitly confirmed Copilot text
  polish, not `azure`. Copilot is not an audio recognizer.
- **Pick local MLX model:** `mlx_model.path` is the installed local model
  directory; `mlx_model.repo` and `mlx_model.hf_endpoint` are only for download.
- **Pick faster-whisper model:** use the separate `faster_whisper_model` section
  only when `backend` is `faster-whisper`.
- **Change hotkey:** `hotkey` (e.g. `f9`).
- **Turn on polish:** `polish` chooses `off` / `auto` / a category key;
  `polish_engine` chooses the implementation (`rules`, `ollama`, `azure`, `copilot`).
  `copilot` uses a separate GitHub device login (`bubble-buddy auth login copilot`)
  for **text polish only**, not audio transcription. Set `copilot_model` (default
  `gpt-5.6-luna`); `bubble-buddy auth models copilot` lists compatible enabled models.
  Recommended defaults: `copilot_reasoning_effort: low` (`low`/`medium`/`high`)
  and `copilot_max_output_tokens: 2048` (16–16384, including reasoning tokens).
  Luna uses Responses with concise output; only completed, validated text is
  delivered. Existing explicit model choices are preserved, not auto-migrated.
  Pair with local `faster-whisper`/`mlx` to avoid Azure. Transcript and selected
  context still go to Copilot; plan/organization quotas and policies apply.
  See the bundled [Copilot setup](accounts.md).
  `polish_categories` contains the editable category definitions.

## Common direct edits

Local MLX transcription:

```json
{
  "speech": { "backend": "mlx" },
  "mlx_model": {
    "type": "mlx",
    "path": "models/mlx-whisper-large-v3-turbo",
    "repo": "mlx-community/whisper-large-v3-turbo",
    "hf_endpoint": "https://hf-mirror.com"
  }
}
```

Azure transcription + Azure polish:

```json
{
  "speech": { "backend": "azure" },
  "polish": { "mode": "auto", "engine": "azure" },
  "azure": {
    "endpoint": "https://<your-resource>.cognitiveservices.azure.com/",
    "auth": "aad"
  }
}
```

## Azure multi-tenant gotcha (`Token tenant ... does not match resource tenant`)

If the Azure OpenAI resource lives in a **different AAD tenant** than the user's
default sign-in (common with a corporate `az login`), requests fail with HTTP 400
`Token tenant <id> does not match resource tenant`. This is NOT fixed by switching
accounts — Bubble Buddy steers credentials at the *resource* tenant:

- **v0.1.6+ auto-discovers** the resource tenant from `azure.endpoint`; usually no
  config is needed.
- To set it explicitly, use **`azure.tenant_id`** (the resource's tenant GUID).
  Also accepted: `azure.tenant`, a top-level `tenant_id`/`tenant`, or the
  `AZURE_TENANT_ID` env var. After setting it, sign in again (🔑) to re-mint the token.

See [`runbooks/auth-failure.md`](runbooks/auth-failure.md) §5.

## Azure setup gotcha (deployment names)

`azure.transcribe_deployment` and `azure.chat_deployment` default to
`gpt-4o-transcribe` / `gpt-4.1`. Transcription only works if the user's Azure
OpenAI resource has **deployments with exactly those names**. If theirs are named
differently, they must override these keys — otherwise requests 404. Always
remind an Azure user to confirm their deployment names when only `endpoint` is
set.

## Guardrails

- Ground keys in the schema and the actual app build. If an unknown key or UI
  mismatch appears, check for a stale skill/build before declaring it unsupported.
- `backend: copilot` and `polish_engine: codex` are not implemented. Do not confuse
  an account provider, a polish category, and a context plugin.
- Never fabricate Azure endpoints or credentials. For `secret` keys, guide the
  user to set them locally; don't ask them to reveal them.
- If a requested change needs source-level detail you don't have, say so and
  point to [`troubleshooting.md`](troubleshooting.md) or the project repository
  rather than guessing.
