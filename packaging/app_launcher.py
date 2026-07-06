"""Frozen-app entry point.

When the packaged executable is double-clicked with no arguments we launch the
Qt desktop overlay (the click-to-use experience). Any explicit CLI arguments are
still forwarded to the normal CLI so ``copilot-voice-shell.exe transcribe ...``
keeps working.
"""

import multiprocessing
import sys


def _run() -> None:
    from copilot_voice_shell import main

    argv = sys.argv[1:]
    if not argv:
        argv = ["desktop"]
    main(argv)


if __name__ == "__main__":
    # Required so PyInstaller-frozen apps don't re-launch the GUI in worker
    # subprocesses spawned by libraries that use multiprocessing.
    multiprocessing.freeze_support()
    _run()
