# Releasing Bubble Buddy

Releases are cut by the **Release installers** GitHub Actions workflow
([`.github/workflows/release.yml`](.github/workflows/release.yml)). It builds the
Windows (Azure + Full) installers and macOS (Azure + Full) DMGs and publishes
them as a GitHub Release.

## Cut a release (recommended — one click, auto-incremented)

1. Go to **Actions → Release installers → Run workflow**.
2. Pick the **bump** level (how to increment the latest release tag):
   - `patch` (default) — bug fixes, e.g. `v0.1.1 → v0.1.2`
   - `minor` — new features, e.g. `v0.1.1 → v0.2.0`
   - `major` — breaking changes, e.g. `v0.1.1 → v1.0.0`
   - *(optional)* type an explicit **version** (no leading `v`, e.g. `0.3.0`) to
     override auto-increment.
3. Click **Run workflow**.

The workflow then, with no further input:

- reads the latest `v*.*.*` git tag and computes the next version,
- creates the new tag (via the API, so it does **not** re-trigger the workflow),
- builds all four installers stamped with that version,
- publishes a GitHub Release with the installers attached and auto-generated notes.

**No version number to type, no version-file edits, and no release PR are needed.**

## Alternative — push a tag manually

Pushing a version tag triggers the same build/publish flow:

```bash
git tag v0.2.0
git push origin v0.2.0
```

Use this when you want to pin the exact commit/tag yourself; otherwise prefer the
one-click workflow above.

## What gets published

| Asset | Platform | Backend | Approx size | Offline |
| --- | --- | --- | --- | --- |
| `BubbleBuddy-Setup-<version>.exe` | Windows | Azure (cloud) | ~60 MB | ✗ |
| `BubbleBuddy-Full-Setup-<version>.exe` | Windows | + local Whisper | ~110 MB | ✓ |
| `BubbleBuddy-<version>.dmg` | macOS | Azure (cloud) | ~70 MB | ✗ |
| `BubbleBuddy-Full-<version>.dmg` | macOS | + local Whisper | ~380 MB | ✓ |

## Versioning notes

- The **installer version is derived from the git tag**, not from
  `pyproject.toml`. The one-click workflow auto-increments from the latest tag, so
  you never have to hand-edit a version number to ship.
- `pyproject.toml` / `packaging/installer.iss` carry a version string for local
  dev builds; it may lag behind the latest released tag and does not affect the
  version stamped on released installers.

## The support skill

The Bubble Buddy support skill under [`skills/`](skills/) is distributed straight
from the `main` branch — `npx skills add ThousandOfWind/bubble-buddy` pulls the
latest `main`. **Merging skill changes to `main` is the skill "release"**; it is
independent of the installer version tags above.

After changing `src/bubble_buddy/config.py` or `src/bubble_buddy/i18n.py`,
regenerate the skill knowledge base so the shipped skill stays in sync, and commit
the result:

```powershell
uv run python tools\gen-kb\gen_kb.py
```

This rewrites the generated `skills/bubble-buddy/references/config.schema.json` and
`messages.json` (do not hand-edit those two files).

## Pre-release checklist

- [ ] CI is green on `main` (`uv run python -m unittest discover -s tests`).
- [ ] If `config.py` / `i18n.py` changed, the skill KB was regenerated and committed.
- [ ] Pick the right bump level (`patch` / `minor` / `major`).
