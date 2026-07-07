# Troubleshooting Bubble Buddy

Resolve problems using the derived knowledge files in this folder plus the
human-written runbooks — never guess at internals you can't see.

## Resources

- [`error-catalog.json`](error-catalog.json) — curated map of symptoms → runbook
  id, with keywords for matching.
- [`messages.json`](messages.json) — the app's user-facing message templates
  (`msg.*` / `bubble.*`) in zh + en. When a user quotes text from the UI, match
  it here (ignore `{placeholders}`) to identify what state they're in.
- `runbooks/*.md` — step-by-step fixes.

## Triage workflow

1. **Identify the symptom.** Ask for: what they did, what happened, any exact
   error text, OS, and whether it's a fresh install.
2. **Match.** Search `error-catalog.json` keywords and, if they quoted UI text,
   `messages.json`. Pick the most specific runbook.
3. **Run the runbook.** Walk the user through it one step at a time; confirm the
   result of each step before continuing.
4. **Escalate only when needed.** If no runbook fits, gather logs (see below)
   and, if source-level detail is required, point at the specific file/symbol in
   the repository for on-demand lookup — do NOT guess at internals you can't see.

## Where the logs & state live

- App config: `%USERPROFILE%\.copilot-voice-shell\config.json` (Win) /
  `~/.copilot-voice-shell/config.json`.
- Azure auth record: `~/.copilot-voice-shell/auth_record.json`.
- Copilot CLI logs (for session/focus issues): `~/.copilot/logs/`.

## Runbook index

| Symptom | Runbook |
| --- | --- |
| No audio / empty or garbage transcription | [`runbooks/no-audio.md`](runbooks/no-audio.md) |
| Azure sign-in / auth failures | [`runbooks/auth-failure.md`](runbooks/auth-failure.md) |
| Black console window flashes at startup | [`runbooks/console-flash.md`](runbooks/console-flash.md) |
| Global hotkey stops working | [`runbooks/hotkey-dead.md`](runbooks/hotkey-dead.md) |
| Model download fails / stuck | [`runbooks/model-download-fail.md`](runbooks/model-download-fail.md) |

## Guardrails

- Never ask a user to paste secrets (Azure keys). For auth, use the in-app
  “Sign in to Azure” flow.
- Prefer the least destructive fix first (restart, re-sign-in) before
  reinstalling or editing config.
- State the app version if known; console-flash is fixed in builds ≥ the
  PR-#6 release, so on old builds the fix is “update”.
