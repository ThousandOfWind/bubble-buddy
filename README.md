# copilot-voice-shell

Small local prototype for a voice shell around Copilot workflows.

## What it does

- Records from the default microphone with `sounddevice` (cross-platform, no external tools required)
- Transcribes locally with `faster-whisper`
- Prints the transcript in a Copilot-friendly format
- Can output plain text, copy it to the clipboard, or paste it into the active app
- Can submit the pasted text immediately, which is useful for Copilot CLI
- Supports custom phrase replacements for terms like `skill`, `Copilot`, and `Claude Code`
- Can pre-download a Whisper model so first use is predictable
- Can run as a global hotkey listener and use your chosen Whisper model
- Includes an experimental cross-platform Qt desktop overlay for macOS and Windows
- Optional Azure OpenAI backend for cloud transcription and LLM text polishing, using your signed-in Azure user credential (no API key stored)

## Requirements

- macOS or Windows
- Python 3.10+
- network access for the first Whisper model download
- (optional) `ffmpeg` for decoding uncommon audio formats passed to `transcribe`
- On Apple Silicon you can additionally use the `mlx` backend for GPU acceleration
  (`mlx-whisper` is installed only on macOS)
- (optional) an Azure OpenAI resource and the Azure CLI (`az login`) to use the `azure` backend/polish engine

## Configuration

Defaults for the language, model, backend, hotkey, polishing, and Azure settings can be set
in a `config.json` file. The CLI looks for it (first match wins) in:

1. the path in the `COPILOT_VOICE_SHELL_CONFIG` environment variable
2. `./config.json` in the current directory
3. `config.json` in the project root
4. `~/.copilot-voice-shell/config.json`

Copy `config.example.json` to `config.json` and edit it. `config.json` is gitignored so
local settings stay out of source control. Command-line flags always override config values.

You can also edit every setting from the desktop overlay: click **⚙ Settings** to open a
panel, change any value (backend, language preference, polish, model, hotkey, Azure
deployments, etc.), and click **Save**. Changes are written to `config.json` and applied to
the running overlay immediately (the hotkey is re-registered automatically).

`max_record_seconds` (default `120`) caps a single continuous recording/streaming session:
if you start recording and never stop, it auto-stops after this many seconds to avoid
accidental long captures. Set it to `0` to disable the limit.

## Azure OpenAI backend (cloud transcription + polishing)

Set `backend` to `azure` (transcription) and/or `polish_engine` to `azure` (LLM cleanup) in
`config.json`, and fill in the `azure` section:

```json
{
  "backend": "azure",
  "polish": "copilot",
  "polish_engine": "azure",
  "azure": {
    "endpoint": "https://<your-resource>.cognitiveservices.azure.com/",
    "api_version": "2025-03-01-preview",
    "auth": "aad",
    "transcribe_deployment": "gpt-4o-mini-transcribe",
    "transcribe_mode": "batch",
    "realtime_api_version": "2025-04-01-preview",
    "chat_deployment": "gpt-4.1"
  }
}
```

`transcribe_mode` controls how audio is transcribed:

- `batch` (default): one request, result returned when the whole clip is processed.
- `stream`: server-sent streaming of the transcription response (partial text as it arrives).
- `realtime`: uses the Azure OpenAI **Realtime API** (WebSocket) transcription session.
  It needs a realtime-capable api-version — set via `realtime_api_version`
  (`2025-04-01-preview` works; the GA `2025-08-28` is not accepted on all resources).
  Requires the `websockets` package (already a dependency).

Authentication defaults to `aad`, which uses your signed-in Azure user credential
(`az login`) via `DefaultAzureCredential` — no secret is stored or committed. To use an API
key instead, set `"auth": "api_key"` and put the key in the env var named by `api_key_env`
(default `AZURE_OPENAI_API_KEY`).

Then use the backend on the command line (flags override config):

```bash
uv run copilot-voice-shell transcribe recordings/example.wav \
  --backend azure --polish copilot --polish-engine azure --plain
```

## Install

```bash
cd copilot-voice-shell
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

Run the cross-platform Qt desktop overlay:

```bash
uv run copilot-voice-shell desktop --hotkey f9 --paste --model small
```

On macOS, the older AppKit overlay is still available:

```bash
uv run copilot-voice-shell overlay --hotkey f9 --paste --model small
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