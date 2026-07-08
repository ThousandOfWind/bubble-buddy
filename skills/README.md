# Bubble Buddy — Support skill

A single installable **support skill** that helps end users install, configure,
use and troubleshoot **Bubble Buddy** as if talking to a knowledgeable
customer-service agent. It is distributed as one npm package and dropped into a
Copilot/agent runtime.

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
  package.json                  ← the single npm package (@bubble-buddy/skills)
  bin/
    install.js                  ← `npx @bubble-buddy/skills` self-registers the skill
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
**trigger** the skill; it does not have to match the npm package name.

## Registering the skill

- **End users (recommended, no clone):** `npx @bubble-buddy/skills` — downloads
  the whole package (SKILL.md **and** every `references/` file) and runs the
  bundled `bin/install.js`, which registers the materialized skill directory with
  your Copilot CLI. Re-run to update.
- **Local (dev/testing):** `copilot skill add skills\bubble-buddy` — one add
  registers the whole skill straight from the repo checkout.

## The no-source mechanism

Two layers keep source out of the published package:

1. **Generation, not copying.** `tools/gen-kb/gen_kb.py` (at the repo root) reads
   `src/copilot_voice_shell/config.py` and `i18n.py` and emits *facts* — key
   names, defaults, enums, message templates — never code. It runs at dev time /
   in CI, not on the user's machine.
2. **`files` whitelist.** `package.json` ships only the `bubble-buddy/` skill
   folder and the `bin/` installer, so `npm publish` includes just `SKILL.md`,
   the generated/curated `references/`, and `bin/install.js`. Nothing above the
   skill folder — and no `src/` — is ever packed.

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

## Publishing

The package publishes under the `@bubble-buddy` scope. Scoped packages are
private by default, so `--access public` (also set via `publishConfig`) is
required:

```powershell
cd skills
npm publish --access public   # publishes @bubble-buddy/skills
```

`npm pack --dry-run` is a good pre-flight to confirm only the `bubble-buddy/`
folder and `bin/install.js` are included.

### CI publish (recommended)

`.github/workflows/publish-skill.yml` publishes the package automatically. Bump
`version` in `skills/package.json`, then push a matching tag:

```powershell
git tag skills-v0.1.1
git push origin skills-v0.1.1
```

The workflow reads the version from `package.json`, skips if it's already on npm
(so re-runs are safe), and publishes with npm provenance. It needs a repository
secret **`NPM_TOKEN`** — an npm automation / granular access token with
read-write on the `@bubble-buddy` scope. You can also trigger it manually from
the Actions tab (workflow_dispatch).

## Curated vs generated

- **Generated** (do not hand-edit): `config.schema.json`, `messages.json`.
- **Curated** (hand-maintained): `install.md`, `install-guide.json`, `config.md`,
  `usage.md`, `troubleshooting.md`, `error-catalog.json`, all `runbooks/*.md`,
  and the `SKILL.md` entry.
