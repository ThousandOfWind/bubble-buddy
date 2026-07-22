# Runbook: Global hotkey not responding

Symptom: pressing the hotkey (default `f9`) does not start a recording. Two
variants:
- **(A) It worked before but stopped** — often after sleep/resume or long uptime.
- **(B) It never worked after installing the packaged app**, even though the
  same key works when running from source on another machine.

## 0. Get the log first (fastest path)
The app writes an always-on log to
`%USERPROFILE%\.bubble-buddy\logs\bubble-buddy.log` (Win) /
`~/.bubble-buddy/logs/bubble-buddy.log`. Ask the user for tray →
**Copy diagnostics** (复制诊断信息), or open the log directly, and look for
`[hotkey]` lines:

- `[hotkey] listener started for 'f9' (combo='<f9>', alive=True)` — the hook was
  registered. If pressing the key produces **no** following `[hotkey] triggered`
  line, the OS is not delivering the key to our low-level hook → go to §3
  (conflict) and §4 (privilege/integrity mismatch).
- `[hotkey] listener started ... alive=False` or
  `[hotkey] failed to start listener: <error>` — registration itself failed.
  The `<error>` (e.g. an import error for a bundled pynput backend) is the root
  cause; capture it. This is the usual explanation for variant (B).
- No `[hotkey]` line at all — the overlay may not have started, or a second
  instance short-circuited (`Bubble Buddy is already running`). See §5.

## 1. Confirm it's the hotkey, not audio
Use the overlay's on-screen record button. If that records but the hotkey
doesn't, it's a hotkey-registration problem (continue below); if neither works,
switch to [`no-audio.md`](no-audio.md).

## 2. Known cause (variant A): OS drops the hotkey on sleep/resume
The global hook can be silently dropped after sleep/resume or if its listener
thread dies. A watchdog re-arms it automatically, but a full quit + relaunch is
the reliable reset. **Action:** fully quit and relaunch, then test.

## 3. Hotkey conflict
Another app may hold the same global shortcut (so our hook never sees it — the
log shows a started listener but no `triggered`). Change `hotkey` to a free key
(e.g. `f8`) via Settings or config, relaunch, and test.

## 4. Privilege / integrity mismatch (common for variant B)
A low-level Windows keyboard hook in a *normal* process does not receive keys
while an **elevated (Run as administrator)** window is in the foreground (UIPI).
If the hotkey works on the desktop but not while a specific admin app is
focused, that's the cause. **Action:** either run that app un-elevated, or run
Bubble Buddy with matching privileges. If it works nowhere at all, this is not
it — rely on the `[hotkey]` log line instead.

## 5. Single-instance short-circuit
If an autostart copy is already running, a second launch logs `Bubble Buddy is
already running; surfaced the existing window.` and exits — the *first* instance
owns the hotkey. Confirm only one instance is intended; fully quit all copies
(tray → Quit) and relaunch once.

## 6. Verify the configured key
Check `hotkey` in config matches what the user presses (see
[`../config.md`](../config.md)). The log prints the normalized `combo` actually
registered — compare it to the key being pressed.

## 7. Still failing — collect and escalate
Gather the **Copy diagnostics** block (OS, app version, the `[hotkey]` lines,
`pynput` version, whether restart helps). Source-level detail lives in
`src/bubble_buddy/qt_overlay.py` (`start_hotkey`, `_ensure_hotkey_alive`) and the
listener status is logged via `src/bubble_buddy/diagnostics.py`.
