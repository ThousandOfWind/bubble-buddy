# Installing & updating Bubble Buddy

Ground edition/wizard facts in [`install-guide.json`](install-guide.json) — don't
invent filenames or wizard options.

## Before installing — scan, then confirm preferences

**Don't jump straight to a download.** First gather context, then confirm the two
choices that actually change what you install and how you configure it. Recommend
a default from the scan instead of asking blindly.

**1. Scan the environment (do this yourself):**
- OS + arch — Windows, or macOS Apple Silicon vs Intel.
- Already installed / running? Existing `~/.bubble-buddy/config.json` to preserve?
- Network / proxy (affects downloads and the model mirror `hf_endpoint`).
- Does the user have **Azure OpenAI access** (an endpoint they can sign in to)?

**2. Confirm the transcription trade-off → picks the edition + `speech.backend`:**

| User priority | Recommend | Edition | `speech.backend` |
| --- | --- | --- | --- |
| Fast, tiny download, best accuracy; has/can get Azure access | **Azure** | Azure (lean) | `azure` |
| Private / offline, no cloud account, free to run (larger download, uses local CPU/GPU, can be slower) | **Local** | Full | `mlx` (Apple Silicon) or `faster-whisper` (Windows/Intel) |

Azure needs a one-time browser sign-in and has per-use cloud cost; local has none
but a bigger install and heavier local compute.

**3. Confirm polish (AI cleanup of the dictated text) → picks `polish.mode` + `polish.engine`:**

| User wants | `polish.mode` | `polish.engine` | Cost |
| --- | --- | --- | --- |
| Raw text only, fastest | `off` | — | none |
| Best quality cleanup, has Azure | `auto` | `azure` | a cloud LLM call (extra latency + cost) |
| Light cleanup, offline & instant | `auto` | `rules` | none |
| Offline LLM cleanup | `auto` | `ollama` | needs Ollama running + a local model |

Only the **Azure** engine needs Azure access; `rules` / `ollama` run locally and
pair well with the Full edition. If the user is unsure, default polish to the
engine that matches their backend (Azure backend → `azure`; local backend →
`rules`).

**4. Recommend + confirm, then proceed** with the matching edition download, the
config below, and (for Azure) the sign-in — all steps in the sections that follow.

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
- Wants local transcription / no cloud account / privacy → **Full**.

## macOS install flow

If you have shell/file tools, **perform these steps for the user** instead of
only describing them:

```bash
# Full local-model edition (downloads the latest matching DMG into /tmp)
mkdir -p /tmp/bubble-buddy-install
gh release download --repo ThousandOfWind/bubble-buddy --pattern 'BubbleBuddy-Full-*.dmg' --dir /tmp/bubble-buddy-install --clobber
hdiutil attach /tmp/bubble-buddy-install/BubbleBuddy-Full-*.dmg
rm -rf "/Applications/Bubble Buddy.app"
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

- Choose `speech.backend: mlx` for Apple Silicon.
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

After an **Azure edition** install (or a **Skip** install that will use Azure),
**don't** just tell the user to click around. Configure it for them, then hand
off only the one step that truly needs them (the browser sign-in).

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

   Adjust `hotkey` / `language_preference` to what the user asked for. The
   `transcribe_deployment` / `chat_deployment` defaults assume the user's Azure
   resource has deployments with those names — override them if theirs differ.
3. **Restart the app** so it reloads the config (fully quit the overlay +
   background process first).
4. **Hand off the one manual step — the sign-in.** AAD sign-in must open a
   browser the user completes (there is no CLI sign-in command), so the login
   itself is theirs to finish. Tell them exactly where it is:
   - When not signed in, a prominent **🔑 Sign in to Azure** (🔑 登录 Azure)
     banner (orange) shows **above the pet**, and is visible in **both the
     collapsed and expanded** states — no need to expand first. It hides
     automatically once signed in.
   - Click it; a browser opens for interactive sign-in. On success the overlay
     shows "Signed in to Azure" and the auth record persists at
     `~/.bubble-buddy/auth_record.json` (sign-in survives restarts).
5. **Verify.** Press the hotkey (default **F9**), speak, and confirm text is
   transcribed into the active app.

## Update

- Updating = download the latest installer of the **same edition** and run it
  over the existing install. On the config page, keep the default **Skip** to
  preserve the existing `config.json` (Basic/Import overwrite it).
- Before updating, fully quit Bubble Buddy (tray/overlay + background process),
  otherwise the running `.exe` can stay locked. Reboot if unsure.
- "Black console flash at startup" is fixed in recent builds — updating is the
  fix (see [`runbooks/console-flash.md`](runbooks/console-flash.md)).

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
- Never ask for Azure secrets during install; the endpoint is not a secret, the
  key/credential is handled by in-app sign-in.
