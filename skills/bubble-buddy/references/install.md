# Installing & updating Bubble Buddy

Ground edition/wizard facts in [`install-guide.json`](install-guide.json) — don't
invent filenames or wizard options.

## Before installing — confirm platform, route and components

**Do not download, change engines/config, or start login until these choices are
confirmed.** Safe environment inspection can help you recommend, but the host
running the assistant may not be the user's target computer. Reuse answers the
user already gave; ask only for missing facts.

A concise opening in the user's language:

> 1. 装在 **Windows 还是 macOS**？如果是 Mac，是 **M 系列还是 Intel**？
> 2. 想走 **Azure、本地，还是 code agent 账号**？可以混用。
> 3. 如果用账号，是 **GitHub Copilot、Codex/ChatGPT、Claude Code，还是其他**？
>    Copilot 目前只做文字润色，音频仍需本地或 Azure 转写。

Then confirm **audio → text** and **text polishing** separately. A short proposal
can settle both, e.g. “Windows + 本地 Whisper 转写 + Copilot 润色，需要 Full 版；
文字会上云、音频留在本地，可以吗？” Do not silently infer cloud consent from the
presence of a Copilot login or an Azure endpoint.

**1. Target platform and safe scan:**
- Confirm Windows/macOS and architecture. Mac M-series → local MLX; Windows or
  Intel Mac → local faster-whisper. Intel/ARM/x64 installer compatibility must
  be verified against actual assets; do not invent an unavailable build.
- Check existing installation/running process and config to preserve. Inspect
  only relevant non-secret settings; do not dump a whole credential-bearing file.
- Check network/proxy and existing local model cache; don't initiate auth yet.

**2. Choose the audio recognizer:**

| Confirmed choice | Edition | `backend` | Boundary |
| --- | --- | --- | --- |
| Local/offline recognition | Full | `mlx` on Apple Silicon; `faster-whisper` on Windows/Intel | Weights download separately; uses local CPU/GPU |
| Azure speech recognition | Azure (lean) or Full | `azure` | Audio goes to Azure; endpoint/deployment/role and usage cost apply |
| Explicit Codex experiment | Compatible cloud build | `codex` | Historical-source route, not live-verified; batch <=120 s, no stable-service promise |
| GitHub Copilot / Claude / other account as recognizer | **Do not select** | No such implemented backend | Copilot has no verified audio transcription channel here |

**3. Choose text polish independently:**

| Confirmed choice | `polish` | `polish_engine` | Boundary |
| --- | --- | --- | --- |
| No model polish | `off` | retain existing value | No polish model call |
| Local light cleanup | `auto` | `rules` | No cloud request |
| Local LLM cleanup | `auto` | `ollama` | Ollama and local model needed |
| Azure text cleanup | `auto` | `azure` | Text/context goes to Azure; role/deployment/cost apply |
| GitHub Copilot text cleanup | `auto` | `copilot` | Text/context goes to Copilot; subscription/model policy applies |

Read [`accounts.md`](accounts.md) for account capabilities and actual login steps.
Codex/Claude are not implemented polish engines. A code agent's context plugin is
not an account-inference backend. Local recognition + Copilot polish is **not
fully offline**, and still needs Full for its local recognizer.

**4. Summarize and confirm:** target OS/chip → edition → recognizer → polish
engine → accounts/models → privacy/cost. If the user is unsure, recommend local
+ rules for offline use, or local + Copilot for an already-authorized account,
and wait for agreement. Do not choose Azure by default.

**5. Check the actual app build before executing.** Verify release assets and
feature availability in release notes/help/Settings. A fresh skill cannot add
Copilot/Codex support to an older binary. If the required build is unavailable,
explain the limitation and offer a supported release/source path for confirmation;
do not fabricate a version or silently change their chosen route.

## Source checkout on a managed device

If the user must acquire Python packages through an approved protected feed,
use [approved-index source installation](approved-index.md). Require an explicit
approved HTTPS index and a checkout containing `tools/install_from_index.py`;
do not assume updating this skill updates their application source. The helper
preserves frozen dependency pins/hashes, handles existing environments only with
explicit opt-in, and requires **`uv run --no-sync`** or direct venv executables
after setup. Do not infer approval from a hostname or change device policy.
This does not apply to binary installer selection or model-weight downloads.

## Editions (pick the right download)

Download from the **Releases page**:
<https://github.com/ThousandOfWind/bubble-buddy/releases/latest>

Bubble Buddy ships in two editions per platform (see `install-guide.json`):

- **Windows Azure (lean, default)** — `BubbleBuddy-Setup-<version>.exe`. Cloud
  transcription via Azure OpenAI. Small download. Requires Azure sign-in.
- **Windows Full** — `BubbleBuddy-Full-Setup-<version>.exe`. Bundles the offline
  Whisper engine. Much larger download.
- **macOS Azure** — `BubbleBuddy-<version>.dmg`. Cloud transcription via Azure
  OpenAI. Small download. Requires Azure sign-in.
- **macOS Full** — `BubbleBuddy-Full-<version>.dmg`. Bundles local inference
  dependencies, but downloads model weights on demand so users can choose their
  model.

Choosing:
- Wants smallest download / already has Azure access → **Azure**.
- Wants local transcription, including local + Copilot polish → **Full**.
- Wants experimental Codex cloud transcription → first verify build support and
  explicit experimental consent; do not invent a separate “code-agent edition”.
- Edition names alone do not choose authentication: only active Azure components
  need Azure sign-in; only active Copilot polish needs Copilot sign-in.

## macOS install flow

If you have shell/file tools, **perform these steps for the user** instead of
only describing them:

```bash
# Only after platform/edition confirmation and checking the asset architecture.
# Fresh Full installation; stop for confirmation if an app already exists.
mkdir -p /tmp/bubble-buddy-install
gh release download --repo ThousandOfWind/bubble-buddy --pattern 'BubbleBuddy-Full-*.dmg' --dir /tmp/bubble-buddy-install --clobber
hdiutil attach /tmp/bubble-buddy-install/BubbleBuddy-Full-*.dmg
if [ -e "/Applications/Bubble Buddy.app" ]; then
  echo "Existing app found: quit it and confirm replacement before updating."
  exit 1
fi
cp -R "/Volumes/Bubble Buddy/Bubble Buddy.app" /Applications/
open "/Applications/Bubble Buddy.app"
```

For Azure lean edition, use pattern `BubbleBuddy-*.dmg` but exclude
`BubbleBuddy-Full-*.dmg` if both are present.

1. Open the DMG.
2. Drag **Bubble Buddy.app** into `/Applications`.
3. Launch **Bubble Buddy.app**.
4. On first launch, Settings opens so the user can choose backend/model/Azure
   settings.

For local model setup on macOS Full:

- Choose `backend: mlx` only for confirmed Apple Silicon.
- On Intel, use `faster-whisper` only with a verified compatible build, not MLX.
- Set `mlx_model.path` to an installed local model directory, or use
  `mlx_model.repo` as the download source (for example
  `mlx-community/whisper-large-v3-turbo`).
- The Full DMG does **not** include model weights; the model downloads on demand
  unless the user points `mlx_model.path` at an existing directory.

If the user wants you to configure local MLX now and a model directory already
exists, write/merge:

```json
{
  "speech": { "backend": "mlx" },
  "mlx_model": {
    "type": "mlx",
    "path": "/absolute/or/project-relative/model/dir",
    "repo": "mlx-community/whisper-large-v3-turbo",
    "hf_endpoint": "https://hf-mirror.com"
  }
}
```

Then relaunch the app. If the model directory does not exist, set `repo` and
`hf_endpoint` and let first use/download fetch it, or run the app's model
download UI when available.

## Install wizard (what each choice means)

The installer installs to `%ProgramFiles%\BubbleBuddy` and, after the standard
steps, asks how to set up config:

1. **Import an existing `config.json`** — point it at a config file to reuse
   (e.g. migrating machines). Skips the language page.
2. **Basic setup (enter Azure endpoint now)** — type the Azure endpoint during
   install; good for the Azure edition when you already know your resource.
3. **Skip — configure in the app** (default) — installs with defaults; you set
   everything later in the ⚙ Settings panel. Shows a one-time interface-language
   choice.

Both **Basic** and **Skip** show a small **interface-language** page; **Import**
does not (it trusts the imported file). On reinstall, the default **Skip** path
never overwrites an existing `config.json`; **Basic setup** and **Import** *do*
replace it, so a returning user with a customised config should pick **Skip**
(or back up their config first) to keep it.

## Azure first-run setup — do it for the user

Only after the user chose an **active Azure recognizer or polisher**, configure
that component and hand off browser authorization. A lean edition label is not
permission to overwrite local/Copilot choices. For code-agent login use
[`accounts.md`](accounts.md), not the Azure flow.

1. **Get the endpoint.** Ask the user only for their Azure OpenAI **endpoint**
   (e.g. `https://<resource>.cognitiveservices.azure.com/`). It is **not** a
   secret. Never ask for an API key — AAD sign-in handles the credential.
2. **Write the config for them.** Create/merge the user config at
   `%USERPROFILE%\.bubble-buddy\config.json` (Windows) or
   `~/.bubble-buddy/config.json` (macOS):

   ```json
   {
     "app": { "hotkey": "f9", "ui_language": "auto" },
     "speech": { "backend": "azure", "language_preference": "zh-en" },
     "polish": { "engine": "azure", "mode": "auto" },
     "azure": {
       "endpoint": "https://<resource>.cognitiveservices.azure.com/",
       "auth": "aad",
       "transcribe_deployment": "gpt-4o-transcribe",
       "chat_deployment": "gpt-4.1"
     }
   }
   ```

   This example is **only for a confirmed Azure + Azure combination**. For
   mixed setups, merge the Azure endpoint/auth settings without changing the
   user's non-Azure recognizer/polisher. Ask for `transcribe_deployment` only for
   Azure recognition and `chat_deployment` only for Azure polish. They are actual
   resource deployment names, not a recommendation to use a model of that age.
   Adjust hotkey/language as agreed and verify the required deployments exist.
3. **Restart the app** so it reloads the config (fully quit the overlay +
   background process first).
4. **Hand off the one manual step — the sign-in.** AAD sign-in must open a
   browser the user completes (there is no CLI sign-in command), so the login
   itself is theirs to finish. Tell them exactly where it is:
   - **Qt desktop:** when not signed in, the **🔑 Sign in to Azure** banner
     appears above the pet in both collapsed and expanded states. It hides
     after successful sign-in.
   - **Legacy native macOS overlay:** expand the pet first, then click **Account**.
     This control is hidden while collapsed or when no provider is needed, but
     remains available in the expanded view after sign-in. In mixed setups it
     routes to the required account; follow the indicated provider and see
     [`accounts.md`](accounts.md), rather than assuming every flow is Azure.
   - For Azure AAD, the control opens browser authorization. On success the
     overlay reports "Signed in to Azure" and persists the auth record at
     `~/.bubble-buddy/auth_record.json` (subject to tenant policy).
5. **Verify in stages.** Check sign-in and resource access first. Use a public
   audio file for a non-microphone test, or ask the user to start a recording.
   Verify output before enabling paste/submit into the intended target app.
   Token refresh does not extend an expired Azure role or override tenant policy.

## Update

- Updating = download the latest installer of the **same edition** and run it
  over the existing install. On the config page, keep the default **Skip** to
  preserve the existing `config.json` (Basic/Import overwrite it).
- Before updating, fully quit Bubble Buddy (tray/overlay + background process),
  otherwise the running `.exe` can stay locked. Reboot if unsure.
- "Black console flash at startup" is fixed in recent builds — updating is the
  fix (see [`runbooks/console-flash.md`](runbooks/console-flash.md)).

## Updating this support skill (the assistant's own knowledge)

This skill's knowledge (runbooks, config schema, versions) is a **snapshot taken
at install time** and does **not** auto-update, so it can go stale (e.g. missing a
newly added config key like `azure.tenant_id`). It is separate from updating the
Bubble Buddy app.

- Refresh the installed skill to the latest published version with:
  ```sh
  npx skills update
  ```
  or re-pull it explicitly:
  ```sh
  npx skills add ThousandOfWind/bubble-buddy
  ```
- Do this whenever the skill's guidance seems to contradict the app (unknown/new
  config keys, "that setting doesn't exist", version mismatches) — a stale skill
  is a common cause of wrong answers. After updating, re-open the assistant so it
  reloads the refreshed references.

## Launch at startup

- Enable in **Settings ▸ General ▸ Launch at startup** (writes the
  `launch_at_startup` config key; on Windows it registers an HKCU Run entry).
- Can also be turned off there later.

## Uninstall

- Use **Windows ▸ Settings ▸ Apps** (or the Start-menu uninstaller) to remove
  Bubble Buddy.
- User data (`~/.bubble-buddy/config.json`, auth record) lives in the
  user profile and may remain after uninstall; delete that folder manually for a
  fully clean removal.

## macOS update/uninstall

- Update: replace `/Applications/Bubble Buddy.app` with the new app from the DMG.
  Existing `~/.bubble-buddy/config.json` is preserved.
- Clean uninstall: quit Bubble Buddy, delete `/Applications/Bubble Buddy.app`,
  and optionally delete `~/.bubble-buddy`.

## Guardrails

- Only reference the filenames/options in `install-guide.json`. If unsure of the
  exact latest version, tell the user to grab the newest release rather than
  guessing a version number.
- Never ask for API keys/access tokens/refresh tokens. Use the selected provider's
  real sign-in on the confirmed target machine; show a fresh device code only
  during an active GitHub authorization flow. See [`accounts.md`](accounts.md).
- Copilot is text polish only. Codex audio is experimental and not live-verified.
  Never promise account login alone provides an audio transcription service.
