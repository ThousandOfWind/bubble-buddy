# Packaging a click-to-use Windows installer

You can freeze the app into a standalone Windows executable and wrap it into a
one-click `Setup.exe` (no Python required on the target machine).

Prerequisites (installed automatically the first time you run the script, or manually):

```powershell
uv add --dev pyinstaller                        # freezes the app
winget install --id JRSoftware.InnoSetup -e     # builds the installer wizard
```

Build everything with one command:

```powershell
pwsh -File packaging\build.ps1
```

Outputs:

- `dist\bubble-buddy\` — portable one-folder build; double-click
  `bubble-buddy.exe` to launch the desktop overlay directly.
- `dist\installer\BubbleBuddy-Setup-0.1.0.exe` — the click-to-run installer
  (adds Start-menu / optional desktop shortcuts and an uninstaller).

## Two editions

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
backend. `-Edition full` sets `BB_INCLUDE_LOCAL=1` for you to bundle it (you can
also set that env var manually). Both editions share the same `AppId`, so a `full`
install upgrades a prior `azure` install in place. In the **Full** edition, open
Settings → **本地 Whisper 模型** to pick a model (tiny…large-v3, or a custom repo id)
and click **⬇ 下载所选本地模型** to fetch it on demand for offline transcription.

Use `-SkipInstaller` to produce only the portable folder. The packaging files live
in `packaging\` (`app_launcher.py` defaults to the `desktop` overlay,
`bubble-buddy.spec` is the PyInstaller spec, and `installer.iss` is the Inno
Setup script). For the Azure `aad` backend, the first launch prompts a one-time
Azure sign-in (see [Azure OpenAI backend](azure.md)); no `az login` or Azure CLI is
required.
