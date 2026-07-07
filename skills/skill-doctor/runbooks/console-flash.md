# Runbook: Black console window flashes at startup

Symptom: when Bubble Buddy launches, a black command-line / console window
briefly appears then disappears.

## Cause
The windowed app can spawn a child process that briefly opens a console. The
most common trigger is Azure credential resolution shelling out to the Azure CLI
(`az.cmd`) during warmup when `backend: azure`.

## Fix — update to a build that suppresses it
- This is fixed in Bubble Buddy builds from the PR-#6 release onward: on Windows
  the app forces child processes to be created with `CREATE_NO_WINDOW`, so no
  console appears.
- **Action:** have the user install the latest installer
  (`BubbleBuddy-Setup-x.y.z.exe`). Confirm the version predates or postdates the
  fix.

## Verify the fix took effect
- After updating, fully close Bubble Buddy (check the tray/overlay and any
  background process) and relaunch. The flash should be gone.
- If they only re-ran the installer over a running instance, the old exe may
  have stayed loaded — reboot or ensure the process is stopped, then relaunch.

## If it STILL flashes on a current build
- Confirm they truly reinstalled the app bundle (not just re-ran a partial
  installer). The fix lives in the frozen Python bundle, so an outdated
  `copilot-voice-shell.exe` will still flash.
- Reduce the trigger: if they don't use Azure, set `backend: faster-whisper` so
  no Azure CLI resolution happens at startup.
- For source-level detail, reference
  `src/copilot_voice_shell/platform_services.py:suppress_child_console_windows`
  and its call sites in `app_launcher.py` / `qt_overlay.py`.
