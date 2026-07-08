# Using Bubble Buddy

A friendly walkthrough of the everyday commands. Every command is copy-paste
ready. Flags always override your `config.json`.

> New here? The easiest way to install and configure Bubble Buddy is through the
> [support skills](../../README.md) — they walk you through it conversationally. This
> guide is for driving the CLI directly.

## Check your setup

Confirm local prerequisites are in place:

```bash
uv run bubble-buddy doctor
```

## Pre-download a model (offline / local Whisper)

```bash
uv run bubble-buddy download-model
```

The CLI defaults to a mirror endpoint for the first model download:

```bash
HF_ENDPOINT=https://hf-mirror.com
```

## Record and use the transcript

Record, transcribe, and copy the result to the clipboard:

```bash
uv run bubble-buddy capture --copy
```

Record, transcribe, and paste plain text into the active app:

```bash
uv run bubble-buddy capture --plain --paste
```

Record, transcribe, paste into Copilot CLI, and press Enter automatically:

```bash
uv run bubble-buddy capture --plain --submit
```

## Global hotkey

Press `cmd+shift+space` once to start recording, then again to stop and paste the
transcript into the current app:

```bash
uv run bubble-buddy hotkey --model small --replacements-file replacements.example.json
```

Use a custom hotkey and submit immediately after pasting:

```bash
uv run bubble-buddy hotkey --hotkey ctrl+alt+r --submit
```

## Desktop overlay

Run the cross-platform Qt desktop overlay:

```bash
uv run bubble-buddy desktop --hotkey f9 --paste --model small
```

On macOS, the older AppKit overlay is still available:

```bash
uv run bubble-buddy overlay --hotkey f9 --paste --model small
```

## Transcribe an existing file

```bash
uv run bubble-buddy transcribe recordings/example.m4a --language zh --model small
```

Use a replacements file to fix recurring ASR mistakes:

```bash
uv run bubble-buddy transcribe recordings/example.m4a \
  --replacements-file replacements.example.json
```

## Send text to Copilot CLI

Send an existing prompt into the active Copilot CLI window:

```bash
uv run bubble-buddy send "Summarize the current diff and suggest the next edit" --submit
```

---

Configuring Azure, custom models or plugins? See the
[developer docs](../../../docs/README.md).
