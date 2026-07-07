# Context plugins

When you dictate, the app inspects the focused window and feeds a compact
"active context" to the polisher so it adapts to what you're doing (VS Code
editor vs. Copilot CLI terminal, which Teams conversation, which web page).
**Context plugins** let you extend what gets gathered per app.

A built-in `copilot_cli` plugin detects a Copilot CLI session running inside a
VS Code integrated terminal and loads the **recent conversation transcript** into
the context, so dictated instructions are translated/cleaned up consistently with
the terms already used in that session.

## Writing a plugin

Drop a `*.py` file into `~/.copilot-voice-shell/plugins/` (or the directory named
by the `CVS_PLUGINS_DIR` environment variable). The file must expose a
module-level `PLUGIN` (an instance), `PLUGINS` (a list), or a `register()`
callable that returns instances. Each plugin implements a tiny contract:

```python
from copilot_voice_shell.context_plugins import PluginInput, PluginResult

class MyAppPlugin:
    name = "my_app"          # unique id (used to disable it via config)

    def matches(self, ctx: PluginInput) -> bool:
        # ctx exposes: system, app_name, exe_path, hwnd, title, sub_kind,
        # content, ancestry
        return "myapp" in ctx.exe_path.lower()

    def extract(self, ctx: PluginInput) -> PluginResult | None:
        return PluginResult(name=self.name, label="My App", text="...context...")

PLUGIN = MyAppPlugin()
```

Plugins are best-effort and fully sandboxed against failure: a slow or broken
plugin can never block or crash dictation.

## Disabling a plugin

Disable any plugin (including a built-in one) by adding its `name` to a
`disabled_context_plugins` list in `config.json`, e.g.
`"disabled_context_plugins": ["copilot_cli"]`.
