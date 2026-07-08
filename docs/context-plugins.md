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

Plugins are best-effort and guarded against failure: each call is wrapped in
error handling, so a plugin that raises can't crash dictation. They run inline
during context gathering with no timeout, though, so a plugin should keep
`matches`/`extract` fast and avoid blocking work (network, slow I/O).

## Disabling a plugin

A user-directory plugin is active because its file is present — remove (or move)
its `*.py` file from the plugins directory to disable it.

Built-in **catalog** plugins are governed instead by the `enabled_plugins`
allow-list in `config.json` (and the in-app settings UI): when `enabled_plugins`
is set, only the listed catalog plugins are active; otherwise the catalog's
`DEFAULT_ENABLED` plugins run.
