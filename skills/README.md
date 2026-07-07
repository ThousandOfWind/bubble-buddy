# Bubble Buddy — Support Skills

A family of installable **support skills** that help end users install,
configure and troubleshoot **Bubble Buddy** as if talking to a knowledgeable
customer-service agent. Skills are distributed via `npm` and can be dropped into
a Copilot/agent runtime.

> **Design guarantee: no application source code is shipped inside a skill.**
> Skills carry only *distilled knowledge* — a config schema, a message catalog
> and human-written runbooks — that is generated from the source at build time.
> When a rare, source-level answer is needed, a skill points the agent at a
> specific file/symbol for on-demand lookup in the repository; it never bundles
> the code.

## Layout

```
skills/
  README.md                     ← this file
  tools/gen-kb/gen_kb.py        ← dev/CI extractor: source → derived JSON
  kb/                           ← canonical generated knowledge base
    config.schema.json
    messages.json
  skill-config/                 ← "how do I configure X?" skill
    SKILL.md
    package.json                ← files-whitelist: SKILL.md + resources only
    resources/config.schema.json
  skill-doctor/                 ← "something is broken" skill
    SKILL.md
    package.json
    resources/error-catalog.json   (curated, hand-maintained)
    resources/messages.json        (generated)
    runbooks/*.md
  skill-installer/              ← "how do I install / update / uninstall?" skill
    SKILL.md
    package.json
    resources/install-guide.json   (curated)
  bubble-buddy-support/         ← front-desk router → installer / config / doctor
    SKILL.md
    package.json
```

The `bubble-buddy-support` router skill triages a request and defers to the
installer/config/doctor specialists (published as sibling npm packages).

## The no-source mechanism

Two layers keep source out of the published package:

1. **Generation, not copying.** `tools/gen-kb/gen_kb.py` reads
   `src/copilot_voice_shell/config.py` and `i18n.py` and emits *facts* — key
   names, defaults, enums, message templates — never code. It runs at dev time
   / in CI, not on the user's machine.
2. **`files` whitelist.** Each skill's `package.json` lists an explicit `files`
   array so `npm publish` includes **only** `SKILL.md` and the generated/curated
   `resources/` (+ `runbooks/`). Nothing above the skill folder — and no `src` —
   is ever packed.

## Regenerating the knowledge base

Run from the repo root after any change to `config.py` or `i18n.py`:

```powershell
.\.venv\Scripts\python.exe skills\tools\gen-kb\gen_kb.py
```

This rewrites `skills/kb/*` and copies into each skill's `resources/`:

- `config.schema.json` — every config key with default / type / enum / note, and
  `secret: true` on sensitive keys (e.g. `azure.api_key`). Nested `azure.*` keys
  are flattened to dotted names.
- `messages.json` — `msg.*` / `bubble.*` UI templates (zh + en) so the doctor
  skill can recognise text a user quotes from the app.

Commit the regenerated JSON alongside the source change so the shipped skills
stay in sync with the app.

## Publishing (per skill)

```powershell
cd skills\skill-config   # or skill-doctor
npm publish --access public
```

`npm pack --dry-run` is a good pre-flight to confirm only whitelisted files are
included.

## Curated vs generated

- **Generated** (do not hand-edit): `config.schema.json`, `messages.json`.
- **Curated** (hand-maintained): `error-catalog.json`, all `runbooks/*.md`, and
  the `SKILL.md` instructions.
