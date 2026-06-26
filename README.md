# copilot-voice-shell

Small local prototype for a voice shell around Copilot workflows.

## What it does

- Records from the default macOS microphone with `ffmpeg`
- Transcribes locally with `faster-whisper`
- Prints the transcript in a Copilot-friendly format
- Optionally copies the transcript to the clipboard

## Requirements

- macOS
- `ffmpeg`
- Python 3.10+
- network access for the first Whisper model download

## Install

```bash
cd /Users/zhuzhirui/vscodeworkspace/copilot-voice-shell
uv sync
```

## Quick start

Check local prerequisites:

```bash
uv run copilot-voice-shell doctor
```

Record, transcribe, and copy the result to the clipboard:

```bash
uv run copilot-voice-shell capture --copy
```

The CLI defaults to this mirror endpoint for the initial model download:

```bash
HF_ENDPOINT=https://hf-mirror.com
```

Transcribe an existing audio file:

```bash
uv run copilot-voice-shell transcribe recordings/example.m4a --language zh --model small
```

## Next steps

- Add hotkey-based push-to-talk
- Inject text directly into a Copilot CLI session
- Add a phrase replacement layer for terms like `skill`, `Copilot`, and `Claude Code`