"""Current web page title.

When the focused surface is a browser, adds the current page's title to the
context so the polisher knows roughly what page you're dictating about. A small,
safe example of a per-app plugin — copy it as a starting point for your own.

Disabled by default; install it with ``bubble-buddy plugins install
browser_page``.
"""

from __future__ import annotations

from ..context_plugins import PluginInput, PluginResult

DEFAULT_ENABLED = False


class BrowserPagePlugin:
    name = "browser_page"
    label = "当前网页"

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
            label=self.label,
            text=f"当前网页：{page}",
        )


PLUGIN = BrowserPagePlugin()
