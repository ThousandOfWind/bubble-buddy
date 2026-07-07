# Configuration

Bubble Buddy reads defaults for language, model, backend, hotkey, polishing and
Azure settings from a `config.json` file. Command-line flags always override
config values.

## Where the config is loaded from

The CLI looks for `config.json` in this order (first match wins):

1. the path in the `COPILOT_VOICE_SHELL_CONFIG` environment variable
2. `./config.json` in the current directory
3. `config.json` in the project root
4. `~/.copilot-voice-shell/config.json`

Copy `config.example.json` to `config.json` and edit it. `config.json` is
gitignored so local settings stay out of source control.

## Editing from the desktop overlay

You can edit every setting from the desktop overlay: click **⚙ Settings** to open
a panel, change any value (backend, language preference, polish, model, hotkey,
Azure deployments, etc.), and click **Save**. Changes are written to `config.json`
and applied to the running overlay immediately (the hotkey is re-registered
automatically).

## Recording limit

`max_record_seconds` (default `120`) caps a single continuous recording/streaming
session: if you start recording and never stop, it auto-stops after this many
seconds to avoid accidental long captures. Set it to `0` to disable the limit.

## See also

- [Azure OpenAI backend](azure.md) — cloud transcription + LLM polishing
- [Context plugins](context-plugins.md) — extend per-app context extraction
