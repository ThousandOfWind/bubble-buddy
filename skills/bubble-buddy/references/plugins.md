# Writing & installing a context plugin

Bubble Buddy can gather extra **per-app context** while you dictate, so the
polisher adapts to what you're doing. A **context plugin** is a tiny Python file
the user drops in — you (the support agent) can author it for them and install
it end-to-end.

Use this reference when a user asks things like: *"can it read context from
<app>?"*, *"make it pull the current <X> into my dictation"*, *"write me a plugin
for <app>"*, or *"how do I add my own context source?"*

> **Ground every fact here.** The contract, field names, install path and
> disable step below are the real ones. There is **no** install command — the
> only way to add a user plugin is saving a `.py` file into the plugins
> directory (below), and the only way to remove one is deleting that file.

## What a plugin does

While you dictate, the app inspects the focused window and hands each plugin a
**native description** of that surface. A plugin decides whether it applies
(`matches`) and, if so, returns a short block of text (`extract`) that gets fed
into the polish prompt. Each plugin call is wrapped in error handling, so a
plugin that *raises* can't crash dictation. Plugins do, however, run **inline**
during context gathering with no timeout — so keep `matches`/`extract` fast and
avoid blocking work (network calls, slow disk I/O), or you'll delay dictation.

## The contract

A plugin is a small object with a `name`, a `matches()`, and an `extract()`:

```python
from bubble_buddy.context_plugins import PluginInput, PluginResult


class MyAppPlugin:
    name = "my_app"  # unique id (see "Disabling a plugin" below)

    def matches(self, ctx: PluginInput) -> bool:
        # Return True only for the surface this plugin cares about.
        return "myapp" in (ctx.exe_path or "").lower()

    def extract(self, ctx: PluginInput) -> PluginResult | None:
        # Return the extra context, or None if there's nothing useful.
        return PluginResult(
            name=self.name,
            label="My App",            # short human label
            text="…context text…",     # injected into the polish prompt
        )


PLUGIN = MyAppPlugin()  # module-level PLUGIN, PLUGINS (list), or register()
```

The module must expose the plugin as one of:
- `PLUGIN` — a single instance, **or**
- `PLUGINS` — a list of instances, **or**
- `register()` — a callable returning instances.

### `PluginInput` — what `matches`/`extract` receive

Only *native* facts about the focused surface (the plugin interprets them
itself; nothing app-specific is pre-computed for it):

| Field | Type | Meaning |
| --- | --- | --- |
| `system` | str | OS, e.g. `"Windows"` / `"Darwin"` |
| `app_name` | str | Focused application name |
| `exe_path` | str | Executable path (lowercased match is handy) |
| `hwnd` | int | Native window handle (Windows) |
| `title` | str | Window / tab title |
| `sub_kind` | str | `terminal` \| `editor` \| `chat` \| `browser` \| `document` \| `""` |
| `content` | str | Best-effort focused text already gathered |
| `ancestry` | tuple | Raw focused-control ancestry: `(ControlType, Name, ClassName)` tuples |

### `PluginResult` — what `extract` returns

| Field | Meaning |
| --- | --- |
| `name` | The plugin's id (same as `self.name`) |
| `label` | Short human label shown for the context block |
| `text` | The context text injected into the polish prompt |

Return `None` (or an empty `text`) when there's nothing to add.

## Installing it

Save the file as `*.py` into the plugins directory — it is **auto-loaded on the
next run**, no install command needed:

- **Windows:** `%USERPROFILE%\.bubble-buddy\plugins\`
- **macOS / Linux:** `~/.bubble-buddy/plugins/`
- Or the directory named by the `BB_PLUGINS_DIR` environment variable.

Files whose name starts with `_` are skipped. If the folder doesn't exist yet,
create it.

When authoring for a user, write the file straight into that folder for them,
then have them restart Bubble Buddy (or the running CLI/overlay) so it's picked
up.

## Verifying it works

1. Restart Bubble Buddy so the plugins directory is re-scanned.
2. Focus the target app and dictate something.
3. The plugin's `label` appears alongside the gathered context, and the polished
   text should reflect the extra context.

If it doesn't load: a syntax error or import failure makes the app silently skip
that one file (by design). If the user has Python and the app's environment
available (e.g. a source/dev install), running the file once
(`python my_app.py`) surfaces the traceback; otherwise, double-check the file
for typos and that the module exposes `PLUGIN` / `PLUGINS` / `register()`.

## Disabling a plugin

A user-directory plugin is active simply because its file is present, so to turn
one off just **delete (or move) the `.py` file** from the plugins directory and
restart Bubble Buddy. There is no config key that disables a drop-in plugin.

(Built-in *catalog* plugins are a separate mechanism — they're governed by the
`enabled_plugins` allow-list in `config.json` — but your own drop-in files are
controlled purely by their presence.)

## A complete, ready-to-use example

A minimal plugin that adds the browser tab title as context on any browser:

```python
"""Adds the current browser tab title as dictation context."""
from bubble_buddy.context_plugins import PluginInput, PluginResult


class BrowserTitlePlugin:
    name = "browser_title"

    def matches(self, ctx: PluginInput) -> bool:
        return ctx.sub_kind == "browser" and bool(ctx.title)

    def extract(self, ctx: PluginInput) -> PluginResult | None:
        return PluginResult(
            name=self.name,
            label="Browser tab",
            text=f"Current web page: {ctx.title}",
        )


PLUGIN = BrowserTitlePlugin()
```

Save it as `browser_title.py` in the plugins directory, restart, and dictate in
a browser — the polisher now knows which page you're on.

---

Deeper developer background (catalog plugins, the built-in `copilot_cli`
transcript plugin) lives in the [developer docs](../../../docs/context-plugins.md).
