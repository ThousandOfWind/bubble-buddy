# Runbook: No audio / empty or garbage transcription

Symptom: user presses the hotkey and speaks, but nothing is transcribed, the
result is empty, or the first words are dropped / come out as garbage.

## 1. Confirm the mic is working at all
- Check Windows/macOS sound settings: is the correct input device selected and
  is the level moving when they speak?
- Have them try another app (Voice Recorder) to confirm the OS hears the mic.

## 2. Check the hotkey actually triggered a recording
- Watch for the overlay changing to a "listening" state when the hotkey is
  pressed. If it never reacts, this is really a hotkey problem → see
  `hotkey-dead.md`.

## 3. First words missing / garbage on cloud backend
- Known cause with `backend: azure` realtime: the first ~seconds can be dropped
  while the realtime socket connects, producing wrong/garbled text.
- Mitigations:
  - Speak a short lead-in ("嗯…") before the real sentence.
  - Try `azure.transcribe_mode: batch` instead of `realtime`/`stream` (batch
    records fully, then transcribes — no connect-race).
  - Ensure `max_record_seconds` is long enough for the utterance.

## 4. Garbage on local backend
- `backend: faster-whisper` (or `mlx` on Apple silicon) with a very small
  `model` can mis-transcribe. Try a larger `model` (e.g. `small` → `medium`).
- Confirm the model finished downloading — if not, see `model-download-fail.md`.

## 5. Still failing
- Collect: OS, `backend`, `model`, `azure.transcribe_mode`, and one example of
  what was said vs. what appeared.
- If the issue looks internal to capture (e.g. buffer/latency), point at
  `src/copilot_voice_shell/qt_overlay.py` mic-capture path for source-level
  lookup — do not guess at internals.
