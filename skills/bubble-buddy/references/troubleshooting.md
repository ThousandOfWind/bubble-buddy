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
   error text, OS, and whether it's a fresh install. When the error text is hard
   to copy (older builds) or the app "does nothing", ask them to use tray →
   **Copy diagnostics** and paste the block — the log tail usually pinpoints the
   cause without further back-and-forth.
2. **Match.** Search `error-catalog.json` keywords and, if they quoted UI text,
   `messages.json`. Pick the most specific runbook.
3. **Apply the fix — do it yourself when you can.** If you have shell/file
   access, carry out the runbook's steps for the user (patch `config.json`, quit
   a stuck process, re-download a model, re-run a command) and confirm each
   result. Only walk the *user* through steps that need them — GUI toggles, the
   in-app Azure sign-in, a UAC prompt. Chat-only? Give copy-paste steps.
4. **Escalate only when needed.** If no runbook fits, gather logs (see below)
   and, if source-level detail is required, point at the specific file/symbol in
   the repository for on-demand lookup — do NOT guess at internals you can't see.

## Where the logs & state live

- **Main diagnostic log: `%USERPROFILE%\.bubble-buddy\logs\bubble-buddy.log`
  (Win) / `~/.bubble-buddy/logs/bubble-buddy.log`.** Always-on rotating log that
  captures startup info, the hotkey listener status (`[hotkey] listener
  started ... alive=True/False`, `[hotkey] failed to start listener: ...`,
  `[hotkey] triggered`), and uncaught tracebacks — even in the packaged
  (windowed) build where console output is otherwise discarded. **This is the
  first thing to look at for almost any issue.**
- **Copy diagnostics:** ask the user to right-click the tray icon →
  **Copy diagnostics** (复制诊断信息) and paste the result. It bundles system
  info + config summary + the recent log tail into one copyable block — use it
  instead of asking users to re-type error text by hand. **Open logs folder**
  (打开日志文件夹) reveals the same log on disk.
- App config: `%USERPROFILE%\.bubble-buddy\config.json` (Win) /
  `~/.bubble-buddy/config.json`.
- Azure auth record: `~/.bubble-buddy/auth_record.json`.
- Copilot CLI logs (for session/focus issues): `~/.copilot/logs/`.

## Runbook index

| Symptom | Runbook |
| --- | --- |
| No audio / empty or garbage transcription | [`runbooks/no-audio.md`](runbooks/no-audio.md) |
| Azure sign-in / auth failures | [`runbooks/auth-failure.md`](runbooks/auth-failure.md) |
| Black console window flashes at startup | [`runbooks/console-flash.md`](runbooks/console-flash.md) |
| Global hotkey stops working, or never works after installing the package | [`runbooks/hotkey-dead.md`](runbooks/hotkey-dead.md) |
| Model download fails / stuck | [`runbooks/model-download-fail.md`](runbooks/model-download-fail.md) |

## Guardrails

- Never ask a user to paste secrets (Azure keys). For auth, use the in-app
  “Sign in to Azure” flow.
- Prefer the least destructive fix first (restart, re-sign-in) before
  reinstalling or editing config.
- State the app version if known; console-flash is fixed in builds ≥ the
  PR-#6 release, so on old builds the fix is “update”.
