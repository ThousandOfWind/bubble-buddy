"""Replace selected import-cache slots without unloading unrelated native modules."""
from contextlib import contextmanager
import sys


@contextmanager
def stub_modules(replacements):
    # patch.dict(sys.modules, ...) restores the ENTIRE dictionary, removing
    # modules imported inside the scope. PyObjC and other native extensions
    # cannot safely be unloaded/reimported that way.
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in replacements}
    sys.modules.update(replacements)
    try:
        yield
    finally:
        for name, module in previous.items():
            if module is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
