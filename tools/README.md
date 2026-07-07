# BB icon tooling

Design/build helper for the **BB** app icon. Not part of the shipped source, but
kept here so the icon can be regenerated or tweaked in the future.

## Generate the app icon

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe tools\generate_bb_icon.py
```

This renders the BB face + voice waveform speech-bubble and writes:

- `packaging\bb.ico` (PNG-compressed ICO, Windows)
- `packaging\bb.icns` (PNG-backed ICNS, macOS)

No Pillow dependency is required. The icon assets are wired into:

- `packaging/copilot-voice-shell.spec` (PyInstaller `EXE(icon=...)` + bundled data)
- `packaging/copilot-voice-shell-macos.spec` (PyInstaller `BUNDLE(icon=...)`)
- `packaging/installer.iss` (`SetupIconFile` / `UninstallDisplayIcon`)
- `src/copilot_voice_shell/qt_overlay.py` (`_load_app_icon` → `QApplication.setWindowIcon`)

Pass `--preview out.png` to also dump a labelled preview sheet (big + small sizes)
instead of only writing the `.ico`.

## Notes

- Must run with the venv Python directly (not `uv run`) if the overlay is running,
  because `uv run` may try to reinstall and hit the locked `.exe`.
- Under `QT_QPA_PLATFORM=offscreen` the font DB is empty; the icon itself uses no
  text, but the `--preview` labels load `C:\Windows\Fonts\msyhbd.ttc`.
