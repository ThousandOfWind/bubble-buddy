# Report an issue / send feedback (escalate to GitHub)

Use this when troubleshooting or usage help has run out of road: either you
**can't resolve the user's problem** with the runbooks/references, or the user
is giving **optimization / feature feedback** (a request to improve, not a bug
you can fix locally). In both cases you can *do it for the user* — open a
well-formed GitHub issue on the project so the maintainer can act on it.

Project repo: **https://github.com/ThousandOfWind/bubble-buddy**
Issues: **https://github.com/ThousandOfWind/bubble-buddy/issues**

## When to offer this

Offer to file an issue (don't do it silently) when:

- No runbook fits and gathered logs don't point at a config/user fix, **or**
- A fix would require a code change / new feature — i.e. it's **optimization or
  feature feedback**, not a support problem, **or**
- The user explicitly asks to "report a bug", "give feedback", "request a
  feature", "提个 issue", "反馈", "报个 bug".

Do **not** file an issue for something a runbook already solves — fix it first.
Always confirm with the user before creating the issue (it's public), and show
them the drafted title + body so they can edit or veto it.

## What to collect first (context / request / expected fix)

Gather these into the template below. Reuse anything already surfaced during the
session — don't re-ask for what you already know. Never include Azure keys or
other secrets; redact them from any pasted log/diagnostics.

1. **Context** — Bubble Buddy version, OS/arch, `backend` / `polish` /
   `polish_engine`, edition, and (for bugs) the exact error text or quoted UI
   message. The quickest source is tray → **Copy diagnostics** (复制诊断信息),
   which already bundles system info + config summary + log tail.
2. **Request** — what the user actually wants, in their words.
3. **Expected / proposed fix** — the user's (or your) idea of a good resolution,
   if there is one. For feedback issues this is the most important field.

## The issue template (local override supported)

The default body lives at [`issue-template.md`](issue-template.md) in this skill.
**Before drafting, check for a user-local override** and prefer it if present:

- Windows: `%USERPROFILE%\.bubble-buddy\issue-template.md`
- macOS/Linux: `~/.bubble-buddy/issue-template.md`

Resolution order:

1. If `~/.bubble-buddy/issue-template.md` exists, load and use it **verbatim** as
   the body skeleton (the user may have customised sections/labels for their
   team).
2. Otherwise use this skill's [`issue-template.md`](issue-template.md).

Fill in every `{placeholder}` you can from the collected context, strip the
`<!-- guidance -->` comments and any placeholder you genuinely can't fill (say so
rather than inventing a value). Derive the **title** from the summary, prefixed
by type — e.g. `[bug] hotkey dead after sleep` or
`[feedback] add per-app hotkey override`.

### Set up a local template (when the user asks)

If the user wants their own template, create the folder + file for them:

```powershell
# Windows
New-Item -ItemType Directory -Force "$env:USERPROFILE\.bubble-buddy" | Out-Null
Copy-Item "<skill>\references\issue-template.md" "$env:USERPROFILE\.bubble-buddy\issue-template.md"
```

```bash
# macOS / Linux
mkdir -p ~/.bubble-buddy
cp <skill>/references/issue-template.md ~/.bubble-buddy/issue-template.md
```

Then tell them to edit that file; the skill will pick it up automatically next
time.

## Creating the issue

**Prefer the `gh` CLI when it's available and authenticated** (do it for the
user); otherwise fall back to a prefilled URL they click.

1. **`gh` CLI (preferred).** Write the filled body to a temp file to preserve
   formatting, then:

   ```bash
   gh issue create \
     --repo ThousandOfWind/bubble-buddy \
     --title "[bug] hotkey dead after sleep" \
     --body-file /tmp/bb-issue.md \
     --label bug
   ```

   - Check auth first: `gh auth status`. If not logged in, either run
     `gh auth login` with the user, or fall back to the URL method.
   - Labels are optional — only pass `--label` values that exist in the repo; if
     unsure, omit `--label` (don't invent labels).
   - After creation, give the user the returned issue URL.

2. **Prefilled URL (fallback, no CLI/auth).** Build a
   `.../issues/new?title=...&body=...` link with URL-encoded title and body and
   give it to the user to open, review and submit:

   ```
   https://github.com/ThousandOfWind/bubble-buddy/issues/new?title=<enc-title>&body=<enc-body>
   ```

   The user stays in control of the final submit, which is fine — the goal is to
   remove the busywork of writing a good report.

## Guardrails

- **Confirm before filing.** Issues are public; show the drafted title + body and
  get a yes.
- **Never include secrets.** Redact Azure keys/tokens from logs and config
  before they go into the body.
- **Don't invent labels, milestones, or assignees.** Omit what you can't verify.
- **One issue per problem.** If the session covered several unrelated problems,
  file (or offer to file) them separately.
- If `gh` isn't installed and the user can't sign in, always fall back to the
  prefilled URL rather than giving up.
