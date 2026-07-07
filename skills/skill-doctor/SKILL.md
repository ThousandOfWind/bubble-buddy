---
name: bubble-buddy-doctor
description: >-
  Diagnose and fix problems a Bubble Buddy user hits: no audio / nothing
  transcribed, Azure sign-in or auth failures, a black console window flashing
  at startup, the global hotkey stopping working, or model-download failures.
  Use when a user reports Bubble Buddy is broken, shows an error message, or
  behaves unexpectedly. Matches quoted UI text to known messages and walks a
  runbook. Knows fixes from the app source WITHOUT shipping the source.
---

# Bubble Buddy — Doctor (troubleshooter)

You triage and resolve **Bubble Buddy** problems. You ship two derived
knowledge files plus human-written runbooks — never the app source.

## Resources

- `resources/error-catalog.json` — curated map of symptoms → runbook id, with
  keywords for matching.
- `resources/messages.json` — the app's user-facing message templates
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
| No audio / empty or garbage transcription | `runbooks/no-audio.md` |
| Azure sign-in / auth failures | `runbooks/auth-failure.md` |
| Black console window flashes at startup | `runbooks/console-flash.md` |
| Global hotkey stops working | `runbooks/hotkey-dead.md` |
| Model download fails / stuck | `runbooks/model-download-fail.md` |

## Guardrails

- Never ask a user to paste secrets (Azure keys). For auth, use the in-app
  “Sign in to Azure” flow.
- Prefer the least destructive fix first (restart, re-sign-in) before
  reinstalling or editing config.
- State the app version if known; console-flash is fixed in builds ≥ the
  PR-#6 release, so on old builds the fix is “update”.
