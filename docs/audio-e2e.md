# Audio-file E2E tests

Two short, public speech recordings are checked in at
[`tests/fixtures/audio/`](../tests/fixtures/audio/README.md): Mandarin AISHELL-1
(4.281 seconds) and English LibriSpeech (8.730 seconds), about 407 KiB combined.
Source commit, transcripts, checksums, attribution and licenses are included.
Nothing needs to be downloaded to obtain the fixtures.

## What this tests

The runner uses the real application functions, not ASR/LLM mocks:

```
checked-in WAV -> cli.transcribe_audio (faster-whisper, CPU/int8)
              -> cli.apply_polish_to_result (Copilot or local rules)
              -> cli.emit_transcription (text file) -> read back exact output
```

This is **audio-file processing E2E**, not microphone/hotkey/global-paste E2E.
It does not open a microphone, read the clipboard, change app focus, paste into
another application or simulate those actions and label them as real coverage.
The final output-file path is the same one used by the application's CLI.

## Run

From the repository root, with dependencies installed:

```bash
# Real local ASR + local rules. No cloud requests.
uv run python tools/audio_e2e.py

# Real local ASR + real Copilot; requires an existing account login.
uv run bubble-buddy auth login copilot
uv run python tools/audio_e2e.py --live-copilot
```

The live flag explicitly opts into Copilot usage (up to one inference per
fixture, with the client's bounded authentication retry). Account quotas and
model policy apply. Only text recognized from the public fixture audio is sent
to Copilot, never private window/session context or the audio itself. Reference
transcripts are assertion-only and are never provided as model input. Login never opens automatically from the
runner. No alternate model/provider or fake result is used when the chosen
model is unavailable.

The default ASR model is cached `small`. If it is missing, the run fails before
inference instead of silently downloading weights. You can allow the download
explicitly or point to a pinned local model:

```bash
uv run python tools/audio_e2e.py --download-model
uv run python tools/audio_e2e.py --asr-model /path/to/faster-whisper-model --live-copilot
```

Weights stay in the normal model cache, **not in this repository**. The runner
uses the app's recommended Copilot model/reasoning/output-budget defaults, or
an explicit `--polish-model`. It uses an isolated in-process config and does not
read or overwrite the user's config, custom prompts or live context. Account
credentials still come from the application's protected Copilot credential store.

Reports and delivered text files go into a new temporary directory printed at
the end. `--output-dir PATH` chooses another directory. Exit status is **0 only
when both fixtures pass**; failures exit 1 and retain a report with the stage,
raw output, errors and timings. If the output directory cannot be created or the
report cannot be written, the failed report is emitted as JSON to stderr and the
runner still exits 1 (no unhandled output-setup exception). Reports are local
artifacts, not golden answers.

## Acceptance criteria

Criteria are checked in alongside the fixed upstream references in `manifest.json`:

- Verify exact SHA-256, bytes, WAV format and frame count before inference.
- Compare Mandarin using character error rate (CER, <=20%) and English using
  word error rate (WER, <=20%). Ignore punctuation/case, not words. An explicitly
  documented traditional-script Mandarin reference is also accepted.
- Raw ASR may contain small phonetic errors; phrase mismatches are **reported**,
  not hidden. For example, `small` has produced `法地产` instead of `房地产` on the
  Mandarin clip. Correcting such an error is a job for the polish stage.
- Final polished text must meet the final error-rate threshold (Chinese <=20%,
  English <=25%) **and** preserve the key phrases listed in the manifest. Thus,
  leaving `法地产` in the final text fails even though its CER is small; dropping
  “cotton” from English also fails even though WER alone might permit one deletion.
- Read the emitted UTF-8 text file back and compare it exactly to the final text.

References/required phrases are used **only for assertions**, never passed as a
prompt, context, replacement map or ASR hint. Real recognized text is passed to
the polish engine. Thresholds permit ordinary punctuation/grammar cleanup but are
only regression heuristics—not proof of arbitrary semantic correctness.

The local-rules baseline can fail final phrase checks when it cannot repair a
recognition error. Do not weaken the final checks or rewrite a fixture to make it
pass. Inspect the raw versus polished stages; use the Copilot run to test the
actual cloud correction path.

Timings distinguish preflight, ASR (including model construction), polish and total
time. The Copilot client can reuse connections between the two cases, so the
second request may be faster. Two clips are smoke/regression coverage, not a
latency benchmark or a representative accuracy evaluation.

## Normal CI

```bash
uv run python -m unittest discover -s tests -v
```

`test_audio_fixtures.py` checks the two real files, pinned transcript provenance,
license metadata and scoring logic without loading Whisper or calling Copilot.
The paid/model-heavy E2E runner is deliberately not auto-discovered as a unit test
and is not run on ordinary pushes or PRs. Never put provider tokens in CI fixtures.
