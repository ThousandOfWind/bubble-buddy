# Context plugins

When you dictate, the app inspects the focused window and feeds a compact
"active context" to the polisher so it adapts to what you're doing (VS Code
editor vs. Copilot CLI terminal, which Teams conversation, which web page).
**Context plugins** let you extend what gets gathered per app.

Built-in `copilot_cli`, `codex_cli`, `claude_code`, and `pi_web` plugins
detect GitHub Copilot CLI, OpenAI Codex, Claude Code, or pi / pi web in
terminals, VS Code agent panels, browser tabs, and supported standalone UIs,
then load the **recent conversation transcript** into the context. Copilot
reads its local SQLite session store, Codex reads rollout JSONL under
`$CODEX_HOME` (or `~/.codex`), Claude reads project transcripts under
`$CLAUDE_CONFIG_DIR/projects` (or `~/.claude/projects`), and pi reads session
JSONL under `PI_CODING_AGENT_SESSION_DIR` or `~/.pi/agent/sessions`. All stores
are accessed read-only.

## Writing a plugin

Drop a `*.py` file into `~/.bubble-buddy/plugins/` (or the directory named
by the `BB_PLUGINS_DIR` environment variable). The file must expose a
module-level `PLUGIN` (an instance), `PLUGINS` (a list), or a `register()`
callable that returns instances. Each plugin implements a tiny contract:

```python
from bubble_buddy.context_plugins import PluginInput, PluginResult

class MyAppPlugin:
    name = "my_app"          # unique id (see "Disabling a plugin" below)

    def matches(self, ctx: PluginInput) -> bool:
        # ctx exposes: system, app_name, exe_path, hwnd, title, sub_kind,
        # content, ancestry
        return "myapp" in ctx.exe_path.lower()

    def extract(self, ctx: PluginInput) -> PluginResult | None:
        return PluginResult(name=self.name, label="My App", text="...context...")

PLUGIN = MyAppPlugin()
```

Plugins are best-effort and guarded against failure: each call is wrapped in
error handling, so a plugin that raises can't crash dictation. They run inline
during context gathering with no timeout, though, so a plugin should keep
`matches`/`extract` fast and avoid blocking work (network, slow I/O).

## Disabling a plugin

A user-directory plugin is active because its file is present — remove (or move)
its `*.py` file from the plugins directory to disable it.

Built-in **catalog** plugins are governed instead by the `enabled_plugins`
allow-list in `config.json`: when `enabled_plugins` is set, only the listed
catalog plugins are active; otherwise the catalog's `DEFAULT_ENABLED` plugins
run.
