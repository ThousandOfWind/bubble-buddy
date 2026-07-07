# Package Bubble Buddy for macOS

The macOS package uses the native AppKit overlay engine so it can appear over
fullscreen Spaces. The build mirrors the Windows flow: PyInstaller creates a
standalone app bundle, then `hdiutil` wraps it into a drag-to-Applications DMG.

## Prerequisites

```bash
uv sync --dev
```

Optional for release signing:

- Apple Developer ID Application certificate installed in Keychain.
- Notary credentials if you plan to notarize outside this script.

## Build

Lean Azure/cloud edition:

```bash
packaging/build_macos.sh --edition azure
```

Full local/offline edition:

```bash
packaging/build_macos.sh --edition full
```

Outputs:

- `dist/macos/Bubble Buddy.app`
- `dist/installer/BubbleBuddy-<version>.dmg`
- `dist/installer/BubbleBuddy-Full-<version>.dmg` for the full edition

Use `--skip-dmg` to produce only the `.app` bundle.

The build embeds an edition-specific first-run `config.json`. On first launch,
the packaged app copies that file to `~/.copilot-voice-shell/config.json` and
sets `COPILOT_VOICE_SHELL_CONFIG` to the user-writable path. The lean `azure`
edition starts with the Azure backend because local Whisper/MLX libraries are
excluded from that bundle; the `full` edition starts with the local MLX backend.
The Full edition bundles local inference dependencies, but not model weights.
For hand-written configs, `mlx_model.path` is the installed MLX model directory,
while `mlx_model.repo` and `mlx_model.hf_endpoint` are only used when fetching
model weights. If you choose the optional CPU/local backend, use a separate
`faster_whisper_model` section instead of mixing it into the MLX config.
Packaged builds set `show_setup_on_first_launch=true`, so the app opens Settings
on first launch as the macOS equivalent of the Windows installer wizard.

## Signing

By default the script ad-hoc signs the app with `codesign --sign -`, which is
enough for local smoke tests but not for distribution.

For a distributable build:

```bash
packaging/build_macos.sh \
  --edition azure \
  --version 0.2.0 \
  --sign-identity "Developer ID Application: Your Team (TEAMID)"
```

Notarization is intentionally not automated yet; run your preferred
`xcrun notarytool submit ...` flow on the generated DMG.

## Runtime permissions

The app bundle declares:

- `NSMicrophoneUsageDescription`
- `NSAppleEventsUsageDescription`

Users may still need to grant these in System Settings:

- **Privacy & Security → Microphone** for recording.
- **Privacy & Security → Accessibility** for paste/submit automation.
- **Privacy & Security → Input Monitoring** for the global hotkey if macOS asks.

If the hotkey does not fire, click the orb to expand and use the Start/Stop
buttons, then grant the prompted permission.

## Editions

| Edition | Bundle contents | Best for |
|---|---|---|
| `azure` | Lean cloud/Azure dependencies; excludes local Whisper/MLX stacks | Small installer, Azure transcription |
| `full` | Includes local MLX/faster-whisper dependencies | Offline/local transcription on Apple Silicon |

The Full edition is larger and may take longer to build because PyInstaller must
collect native MLX/Whisper libraries.

## Release workflow

The GitHub release workflow builds Windows installers and macOS DMGs. Pushing a
`vX.Y.Z` tag builds both macOS editions and attaches the generated DMGs to the
GitHub Release. Manual `workflow_dispatch` runs use the provided version input.
