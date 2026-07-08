# Bubble Buddy — Support skill

A single installable **support skill** that helps end users install, configure,
use and troubleshoot **Bubble Buddy** as if talking to a knowledgeable
customer-service agent. It follows the open
[Agent Skills](https://agentskills.io) format, so it installs into any
compatible agent (GitHub Copilot CLI, Claude Code, Codex, Cursor, Gemini CLI and
[many more](https://github.com/vercel-labs/skills#supported-agents)).

> **Design guarantee: no application source code is shipped inside the skill.**
> It carries only *distilled knowledge* — a config schema, a message catalog and
> human-written guides/runbooks — generated from the source at build time. When
> a rare, source-level answer is needed, the skill points the agent at a specific
> file/symbol for on-demand lookup in the repository; it never bundles the code.

## Layout

Following the [Remotion skill pattern](https://github.com/remotion-dev/skills):
one skill with a small `SKILL.md` entry point that loads deeper **reference**
files on demand (progressive disclosure).

```
skills/
  README.md                     ← this file
  bubble-buddy/                 ← THE skill (name: bubble-buddy)
    SKILL.md                    ← entry: triage + product summary + guardrails
    references/                 ← loaded on demand by the entry file
      install.md                (curated)
      install-guide.json        (curated)
      config.md                 (curated)
      config.schema.json        (generated)
      usage.md                  (curated)
      plugins.md                (curated)
      troubleshooting.md        (curated)
      error-catalog.json        (curated)
      messages.json             (generated)
      runbooks/*.md             (curated, step-by-step fixes)
```

The `SKILL.md` `name: bubble-buddy` is the identifier the agent runtime uses to
**trigger** the skill.

## Installing the skill

- **End users (recommended, no clone):** the cross-agent
  [`skills`](https://github.com/vercel-labs/skills) CLI pulls the whole skill
  straight from GitHub and drops it into your agent's skills directory:

  ```bash
  # Interactive: pick your agent(s) + scope
  npx skills add ThousandOfWind/bubble-buddy

  # Non-interactive for GitHub Copilot CLI, global scope (~/.copilot/skills/)
  npx skills add ThousandOfWind/bubble-buddy -a github-copilot -g -y

  # Refresh an installed copy to the latest version
  npx skills update
  ```

  `npx skills add` discovers `bubble-buddy/SKILL.md` and installs it together
  with every `references/` file. It supports Claude Code, Codex, Cursor, Gemini
  CLI, GitHub Copilot and [60+ agents](https://github.com/vercel-labs/skills#supported-agents),
  placing the files in each agent's expected location.

- **Local (dev/testing):** `npx skills add ./skills` from a repo checkout, or use
  your agent's native add command (e.g. Copilot CLI: `copilot skill add skills/bubble-buddy`).

## The no-source mechanism

Two layers keep source out of the installed skill:

1. **Generation, not copying.** `tools/gen-kb/gen_kb.py` (at the repo root) reads
   `src/copilot_voice_shell/config.py` and `i18n.py` and emits *facts* — key
   names, defaults, enums, message templates — never code. It runs at dev time /
   in CI, not on the user's machine.
2. **Skill folder only.** The `skills` CLI installs the `bubble-buddy/` folder —
   `SKILL.md` and the generated/curated `references/` — and nothing above it. No
   `src/` is ever copied.

## Regenerating the knowledge base

Run from the repo root after any change to `config.py` or `i18n.py`:

```powershell
.\.venv\Scripts\python.exe tools\gen-kb\gen_kb.py
```

This writes directly into the skill's `references/`:

- `config.schema.json` — every config key with default / type / enum / note, and
  `secret: true` on sensitive keys (e.g. `azure.api_key`). Nested `azure.*` keys
  are flattened to dotted names.
- `messages.json` — `msg.*` / `bubble.*` UI templates (zh + en) so the skill can
  recognise text a user quotes from the app.

Commit the regenerated JSON alongside the source change so the shipped skill
stays in sync with the app.

## Curated vs generated

- **Generated** (do not hand-edit): `config.schema.json`, `messages.json`.
- **Curated** (hand-maintained): `install.md`, `install-guide.json`, `config.md`,
  `usage.md`, `troubleshooting.md`, `error-catalog.json`, all `runbooks/*.md`,
  and the `SKILL.md` entry.
