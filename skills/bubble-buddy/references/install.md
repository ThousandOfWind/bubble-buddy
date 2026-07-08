# Installing & updating Bubble Buddy

Ground edition/wizard facts in [`install-guide.json`](install-guide.json) — don't
invent filenames or wizard options.

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
- User data (`~/.copilot-voice-shell/config.json`, auth record) lives in the
  user profile and may remain after uninstall; delete that folder manually for a
  fully clean removal.

## macOS update/uninstall

- Update: replace `/Applications/Bubble Buddy.app` with the new app from the DMG.
  Existing `~/.copilot-voice-shell/config.json` is preserved.
- Clean uninstall: quit Bubble Buddy, delete `/Applications/Bubble Buddy.app`,
  and optionally delete `~/.copilot-voice-shell`.

## Guardrails

- Only reference the filenames/options in `install-guide.json`. If unsure of the
  exact latest version, tell the user to grab the newest release rather than
  guessing a version number.
- Never ask for Azure secrets during install; the endpoint is not a secret, the
  key/credential is handled by in-app sign-in.
