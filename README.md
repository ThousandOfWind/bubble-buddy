# copilot-voice-shell

Small local prototype for a voice shell around Copilot workflows.

## What it does

- Records from the default microphone with `sounddevice` (cross-platform, no external tools required)
- Transcribes locally with `faster-whisper`
- Prints the transcript in a Copilot-friendly format
- Can output plain text, copy it to the clipboard, or paste it into the active app
- Can submit the pasted text immediately, which is useful for Copilot CLI
- Supports custom phrase replacements for terms like `skill`, `Copilot`, and `Claude Code`
- Can pre-download a Whisper model so first use is predictable
- Can run as a global hotkey listener and use your chosen Whisper model
- Includes an experimental cross-platform Qt desktop overlay for macOS and Windows
- Optional Azure OpenAI backend for cloud transcription and LLM text polishing, using your signed-in Azure user credential (no API key stored)

## Requirements

- macOS or Windows
- Python 3.10+
- network access for the first Whisper model download
- (optional) `ffmpeg` for decoding uncommon audio formats passed to `transcribe`
- On Apple Silicon you can additionally use the `mlx` backend for GPU acceleration
  (`mlx-whisper` is installed only on macOS)
- (optional) an Azure OpenAI resource and the Azure CLI (`az login`) to use the `azure` backend/polish engine

## Configuration

Defaults for the language, model, backend, hotkey, polishing, and Azure settings can be set
in a `config.json` file. The CLI looks for it (first match wins) in:

1. the path in the `COPILOT_VOICE_SHELL_CONFIG` environment variable
2. `./config.json` in the current directory
3. `config.json` in the project root
4. `~/.copilot-voice-shell/config.json`

Copy `config.example.json` to `config.json` and edit it. `config.json` is gitignored so
local settings stay out of source control. Command-line flags always override config values.

You can also edit every setting from the desktop overlay: click **⚙ Settings** to open a
panel, change any value (backend, language preference, polish, model, hotkey, Azure
deployments, etc.), and click **Save**. Changes are written to `config.json` and applied to
the running overlay immediately (the hotkey is re-registered automatically).

`max_record_seconds` (default `120`) caps a single continuous recording/streaming session:
if you start recording and never stop, it auto-stops after this many seconds to avoid
accidental long captures. Set it to `0` to disable the limit.

## Azure OpenAI backend (cloud transcription + polishing)

Set `backend` to `azure` (transcription) and/or `polish_engine` to `azure` (LLM cleanup) in
`config.json`, and fill in the `azure` section:

```json
{
  "backend": "azure",
  "polish": "copilot",
  "polish_engine": "azure",
  "azure": {
    "endpoint": "https://<your-resource>.cognitiveservices.azure.com/",
    "api_version": "2025-03-01-preview",
    "auth": "aad",
    "transcribe_deployment": "gpt-4o-mini-transcribe",
    "transcribe_mode": "batch",
    "realtime_api_version": "2025-04-01-preview",
    "chat_deployment": "gpt-4.1"
  }
}
```

`transcribe_mode` controls how audio is transcribed:

- `batch` (default): one request, result returned when the whole clip is processed.
- `stream`: server-sent streaming of the transcription response (partial text as it arrives).
- `realtime`: uses the Azure OpenAI **Realtime API** (WebSocket) transcription session.
  It needs a realtime-capable api-version — set via `realtime_api_version`
  (`2025-04-01-preview` works; the GA `2025-08-28` is not accepted on all resources).
  Requires the `websockets` package (already a dependency).

Authentication defaults to `aad`, which uses your signed-in Azure user credential —
no secret is stored or committed. Sign-in is resolved silently in this order:

1. a **persisted browser sign-in** (an OS-encrypted token cache under
   `~/.copilot-voice-shell`, so it survives restarts — no daily re-login),
2. an existing `az login` / environment / managed-identity credential,
3. a one-time **interactive browser sign-in** (no Azure CLI required).

In the desktop overlay, if you are not signed in a **🔑 登录 Azure** button appears;
clicking it opens the system browser once and then persists the session. The hot
recording path and background token refresh never open a browser unexpectedly. To
use an API key instead, set `"auth": "api_key"` and put the key in the env var named
by `api_key_env` (default `AZURE_OPENAI_API_KEY`).

Then use the backend on the command line (flags override config):

```bash
uv run copilot-voice-shell transcribe recordings/example.wav \
  --backend azure --polish copilot --polish-engine azure --plain
```

## Install

```bash
cd copilot-voice-shell
uv sync
```

## Context plugins

When you dictate, the app inspects the focused window and feeds a compact
"active context" to the polisher so it adapts to what you're doing (VS Code
editor vs. Copilot CLI terminal, which Teams conversation, which web page).
**Context plugins** let you extend what gets gathered per app.

A built-in `copilot_cli` plugin detects a Copilot CLI session running inside a
VS Code integrated terminal and loads the **recent conversation transcript** into
the context, so dictated instructions are translated/cleaned up consistently with
the terms already used in that session.

Write your own plugin by dropping a `*.py` file into
`~/.copilot-voice-shell/plugins/` (or the directory named by the
`CVS_PLUGINS_DIR` environment variable). The file must expose a module-level
`PLUGIN` (an instance), `PLUGINS` (a list), or a `register()` callable that
returns instances. Each plugin implements a tiny contract:

```python
from copilot_voice_shell.context_plugins import PluginInput, PluginResult

class MyAppPlugin:
    name = "my_app"          # unique id (used to disable it via config)

    def matches(self, ctx: PluginInput) -> bool:
        # ctx exposes: system, app_name, exe_path, hwnd, title, sub_kind,
        # content, copilot_cli, session_id, session_summary
        return "myapp" in ctx.exe_path.lower()

    def extract(self, ctx: PluginInput) -> PluginResult | None:
        return PluginResult(name=self.name, label="My App", text="...context...")

PLUGIN = MyAppPlugin()
```

Plugins are best-effort and fully sandboxed against failure: a slow or broken
plugin can never block or crash dictation. Disable any plugin (including a
built-in one) by adding its `name` to a `disabled_context_plugins` list in
`config.json`, e.g. `"disabled_context_plugins": ["copilot_cli"]`.

## Package a click-to-use Windows installer

You can freeze the app into a standalone Windows executable and wrap it into a
one-click `Setup.exe` (no Python required on the target machine).

Prerequisites (installed automatically the first time you run the script, or manually):

```powershell
uv add --dev pyinstaller            # freezes the app
winget install --id JRSoftware.InnoSetup -e   # builds the installer wizard
```

Build everything with one command:

```powershell
pwsh -File packaging\build.ps1
```

Outputs:

- `dist\copilot-voice-shell\` — portable one-folder build; double-click
  `copilot-voice-shell.exe` to launch the desktop overlay directly.
- `dist\installer\CopilotVoiceShell-Setup-0.1.0.exe` — the click-to-run installer
  (adds Start-menu / optional desktop shortcuts and an uninstaller).

### Two editions

The installer ships in two editions — pick with the `-Edition` switch:

```powershell
pwsh -File packaging\build.ps1 -Edition azure   # lean, cloud only (default)
pwsh -File packaging\build.ps1 -Edition full    # also bundles offline Whisper
```

| Edition | Backend | Size (installer) | Offline |
|---|---|---|---|
| `azure` (default) | Azure realtime + LLM polish | ~59 MB | ✗ |
| `full` | + local faster-whisper | ~240 MB | ✓ |

The **Azure** edition is lean because the offline Whisper stack (ctranslate2 +
ffmpeg + onnxruntime, ~185 MB) is excluded; the shipped config uses the Azure
backend. `-Edition full` sets `CVS_INCLUDE_LOCAL=1` for you to bundle it (you can
also set that env var manually). Both editions share the same `AppId`, so a `full`
install upgrades a prior `azure` install in place. In the **Full** edition, open
Settings → **本地 Whisper 模型** to pick a model (tiny…large-v3, or a custom repo id)
and click **⬇ 下载所选本地模型** to fetch it on demand for offline transcription.

Use `-SkipInstaller` to produce only the portable folder. The packaging files live
in `packaging\` (`app_launcher.py` defaults to the `desktop` overlay,
`copilot-voice-shell.spec` is the PyInstaller spec, and `installer.iss` is the Inno
Setup script). For the Azure `aad` backend, the first launch prompts a one-time
Azure sign-in (see below); no `az login` or Azure CLI is required.

## Releasing a new version

Releases are automated by `.github/workflows/release.yml`: pushing a `vX.Y.Z` tag
builds **both** editions on a Windows runner and publishes them as a GitHub Release
with both installers attached.

```bash
# 1. bump the version in pyproject.toml, commit, then:
git tag v0.2.0
git push origin v0.2.0
```

The workflow then: installs uv + Python + Inno Setup, runs `packaging\build.ps1`
twice (`-Edition azure` then `-Edition full`, stamping the tag version), and attaches
`CopilotVoiceShell-Setup-0.2.0.exe` and `CopilotVoiceShell-Full-Setup-0.2.0.exe` to
the release. You can also trigger it manually from the **Actions** tab
(workflow_dispatch) to test a build without tagging.

## Package a macOS app / DMG

The macOS package uses the native AppKit overlay engine for fullscreen Spaces.

```bash
packaging/build_macos.sh --edition azure   # lean cloud edition
packaging/build_macos.sh --edition full    # bundles local MLX/Whisper stack
```

Outputs:

- `dist/macos/Bubble Buddy.app`
- `dist/installer/BubbleBuddy-<version>.dmg`

See [docs/macos-packaging.md](docs/macos-packaging.md) for signing,
notarization, permissions, and release workflow details.


## Quick start

Check local prerequisites:

```bash
uv run copilot-voice-shell doctor
```

Pre-download the default model:

```bash
uv run copilot-voice-shell download-model
```

Record, transcribe, and copy the result to the clipboard:

```bash
uv run copilot-voice-shell capture --copy
```

Record, transcribe, and paste plain text into the active app:

```bash
uv run copilot-voice-shell capture --plain --paste
```

Record, transcribe, paste into Copilot CLI, and press Enter automatically:

```bash
uv run copilot-voice-shell capture --plain --submit
```

Run a global hotkey listener. Press `cmd+shift+space` once to start recording, then again to stop and paste the transcript into the current app:

```bash
uv run copilot-voice-shell hotkey --model small --replacements-file replacements.example.json
```

Run the cross-platform Qt desktop overlay:

```bash
uv run copilot-voice-shell desktop --hotkey f9 --paste --model small
```

On macOS, the older AppKit overlay is still available:

```bash
uv run copilot-voice-shell overlay --hotkey f9 --paste --model small
```

Use a custom hotkey and submit immediately after pasting:

```bash
uv run copilot-voice-shell hotkey --hotkey ctrl+alt+r --submit
```

The CLI defaults to this mirror endpoint for the initial model download:

```bash
HF_ENDPOINT=https://hf-mirror.com
```

Transcribe an existing audio file:

```bash
uv run copilot-voice-shell transcribe recordings/example.m4a --language zh --model small
```

Use a replacements file to fix recurring ASR mistakes:

```bash
uv run copilot-voice-shell transcribe \
  /Users/zhuzhirui/tmp/faster-whisper-test/sample.m4a \
  --replacements-file replacements.example.json
```

Send an existing prompt into the active Copilot CLI window:

```bash
uv run copilot-voice-shell send "Summarize the current diff and suggest the next edit" --submit
```