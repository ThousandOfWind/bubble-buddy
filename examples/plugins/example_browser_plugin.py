"""Example context plugin.

Copy this file into ``~/.copilot-voice-shell/plugins/`` (or the directory named
by the ``CVS_PLUGINS_DIR`` environment variable) and restart the app. It will be
discovered automatically.

This example adds the current web page's title as extra context whenever the
focused surface is a browser, so the polisher knows roughly what page you're
dictating about. Use it as a starting point for your own app-specific plugins.
"""

from __future__ import annotations

from copilot_voice_shell.context_plugins import PluginInput, PluginResult


class BrowserPagePlugin:
    # A unique id. Add it to "disabled_context_plugins" in config.json to turn off.
    name = "example_browser_page"

    def matches(self, ctx: PluginInput) -> bool:
        # ``sub_kind`` is one of: terminal | editor | chat | browser | document | "".
        return ctx.sub_kind == "browser" and bool(ctx.title)

    def extract(self, ctx: PluginInput) -> "PluginResult | None":
        # Strip the trailing " - Browser Name" the OS appends to the window title.
        page = ctx.title.split(" - ")[0].strip()
        if not page:
            return None
        return PluginResult(
            name=self.name,
            label="Current page",
            text=f"The user is looking at the web page: {page}",
        )


# The loader accepts a module-level ``PLUGIN`` (instance), ``PLUGINS`` (list),
# or a ``register()`` callable returning instances.
PLUGIN = BrowserPagePlugin()
