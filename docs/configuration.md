# Configuration

Bubble Buddy reads defaults for language, model, backend, hotkey, polishing and
Azure settings from a `config.json` file. Command-line flags always override
config values.

## Where the config is loaded from

The CLI looks for `config.json` in this order (first match wins):

1. the path in the `BUBBLE_BUDDY_CONFIG` environment variable
2. `./config.json` in the current directory
3. `config.json` in the project root
4. `~/.bubble-buddy/config.json`

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
- [Codex / ChatGPT dictation](codex.md) — experimental `backend: codex`, browser login and automatic refresh; no Azure required when polish uses `rules`/`ollama`
- [GitHub Copilot polishing](copilot.md) — `polish_engine: copilot`; default GPT-5.6 Luna, `copilot_reasoning_effort: low`, `copilot_max_output_tokens: 2048`; pair with local ASR to avoid Azure
- [Context plugins](context-plugins.md) — extend per-app context extraction
