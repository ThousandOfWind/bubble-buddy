# copilot-voice-shell

Small local prototype for a voice shell around Copilot workflows.

## What it does

- Records from the default macOS microphone with `ffmpeg`
- Transcribes locally with `faster-whisper`
- Prints the transcript in a Copilot-friendly format
- Can output plain text, copy it to the clipboard, or paste it into the active app
- Can submit the pasted text immediately, which is useful for Copilot CLI
- Supports custom phrase replacements for terms like `skill`, `Copilot`, and `Claude Code`
- Can pre-download a Whisper model so first use is predictable
- Can run as a global hotkey listener and use your chosen Whisper model

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