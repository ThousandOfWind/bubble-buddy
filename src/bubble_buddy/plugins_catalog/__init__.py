"""Official catalog of context plugins.

Each module in this package is a self-contained context plugin (or set of
plugins) that the user can browse and install. A plugin module should:

* start with a docstring whose first line is a short, human-readable summary
  (shown when listing/installing plugins) and whose body describes what it does;
* expose a module-level ``PLUGIN`` (an instance), ``PLUGINS`` (a list), or a
  ``register()`` callable returning instances;
* optionally set ``DEFAULT_ENABLED = True`` to be active out of the box.

See ``bubble_buddy.context_plugins`` for the plugin contract and the
install/enable machinery.
"""
