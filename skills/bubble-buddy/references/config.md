# Configuring Bubble Buddy

You have a machine-generated schema of every configuration key in
[`config.schema.json`](config.schema.json). Always ground answers in that schema
— never invent keys or defaults.

## Where the config lives

- **Windows:** `%USERPROFILE%\.copilot-voice-shell\config.json`
- **macOS / Linux:** `~/.copilot-voice-shell/config.json`

The file is plain JSON. Missing keys fall back to the defaults in the schema, so
a valid config can contain only the keys the user overrode.

## How to use the schema

`config.schema.json` has this shape:

```json
{
  "keys": {
    "backend": { "default": "faster-whisper", "type": "string",
                 "enum": ["faster-whisper","mlx","azure"], "note": "..." },
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

1. **Read** the current `config.json` (ask them to paste it, or read it if you
   have file access). Treat any `secret: true` value as redacted.
2. **Locate** the relevant key(s) in the schema. Explain default + allowed values.
3. **Validate** the desired value against `type`/`enum`. If invalid, say why and
   list the valid options.
4. **Produce a minimal edit** — show only the key(s) that change, correctly
   nested, and remind them Bubble Buddy re-reads config on next launch (or via
   the ⚙ Settings panel, which is the safest way to edit).
5. **Prefer the Settings UI** for common changes (interface language, backend,
   launch-at-startup) — it validates and applies live. Hand-editing JSON is a
   fallback for advanced keys.

## Common tasks (cheat-sheet)

- **Switch interface language:** `ui_language` (Settings ▸ General ▸ Interface
  language applies it live).
- **Enable start-on-boot:** `launch_at_startup: true` (Settings ▸ General).
- **Pick transcription engine:** prefer `speech.backend` (`mlx` Apple-silicon
  local / `faster-whisper` CPU local / `azure` cloud). Legacy flat key `backend`
  is still accepted. If `azure`, the `azure.*` block must be set.
- **Pick local MLX model:** `mlx_model.path` is the installed local model
  directory; `mlx_model.repo` and `mlx_model.hf_endpoint` are only for download.
- **Pick faster-whisper model:** use the separate `faster_whisper_model` section
  only when `speech.backend` is `faster-whisper`.
- **Change hotkey:** `hotkey` (e.g. `f9`).
- **Turn on polish:** `polish.mode` chooses `off` / `auto` / a category key;
  `polish.engine` chooses the implementation (`rule`, `ollama`, `azure`).
  `polish.categories` contains the editable category definitions.

## Azure setup gotcha (deployment names)

`azure.transcribe_deployment` and `azure.chat_deployment` default to
`gpt-4o-transcribe` / `gpt-4.1`. Transcription only works if the user's Azure
OpenAI resource has **deployments with exactly those names**. If theirs are named
differently, they must override these keys — otherwise requests 404. Always
remind an Azure user to confirm their deployment names when only `endpoint` is
set.

## Guardrails

- Only reference keys present in the schema. If a user names an unknown key, say
  it isn't a recognised Bubble Buddy setting and suggest the closest match.
- Never fabricate Azure endpoints or credentials. For `secret` keys, guide the
  user to set them locally; don't ask them to reveal them.
- If a requested change needs source-level detail you don't have, say so and
  point to [`troubleshooting.md`](troubleshooting.md) or the project repository
  rather than guessing.
