# Runbook: Global hotkey stops responding

Symptom: the hotkey (default `f9`) worked before but no longer starts a
recording. Often after the machine sleeps/resumes or after long uptime.

## 1. Confirm it's the hotkey, not audio
- Open the overlay and use its on-screen control (if any) to start a recording.
  If that works but the hotkey doesn't, it's a hotkey registration problem.

## 2. Known cause: OS drops the global hotkey on sleep/resume
- On Windows the global hotkey can be silently dropped by the OS after
  sleep/resume or if its listener thread dies.
- Bubble Buddy has a watchdog that re-arms the hotkey automatically, but if the
  app has been running a very long time or was suspended oddly, a restart is the
  reliable reset.
- **Action:** fully quit and relaunch Bubble Buddy, then test the hotkey.

## 3. Hotkey conflict
- Another app may have grabbed the same global shortcut. Change `hotkey` to a
  free key (e.g. `f8`) via Settings or config, then relaunch.
- Some keys require the app to run with sufficient privileges to register a
  global hook — if it never works even after restart, try a different key.

## 4. Verify the configured key
- Check `hotkey` in config matches what the user is pressing. Use the
  [`../config.md`](../config.md) to review/validate the value.

## 5. Still failing
- Collect: OS, whether it follows sleep/resume, the `hotkey` value, and whether
  a restart temporarily fixes it. For source-level detail reference
  `src/bubble_buddy/qt_overlay.py` (start_hotkey / hotkey watchdog).
