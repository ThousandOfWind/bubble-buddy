# Developer documentation

Detailed guides for building, configuring and extending Bubble Buddy.

| Guide | What it covers |
|---|---|
| [Configuration](configuration.md) | `config.json`, load order, the Settings panel, recording limit |
| [Azure OpenAI backend](azure.md) | Cloud transcription + LLM polishing, auth, transcribe modes |
| [Codex / ChatGPT dictation](codex.md) | Experimental account-based transcription, pi-style OAuth, refresh and limitations |
| [GitHub Copilot polishing](copilot.md) | Local speech recognition + Copilot account text polish, device login, models and renewal |
| [Audio-file E2E](audio-e2e.md) | Two licensed audio fixtures, real local ASR/Copilot/file output, opt-in live tests |
| [Context plugins](context-plugins.md) | Extend per-app "active context" extraction |
| [Packaging](packaging.md) | Freeze the app into a click-to-use Windows installer |
| [macOS packaging](macos-packaging.md) | Build the macOS `.app` / DMG |
| [Frontend design contract](frontend-design.md) | Shared overlay behaviour across platforms |
| [Releasing](releasing.md) | Tag-driven GitHub Release workflow |

Looking for **end-user** how-tos (install, day-to-day usage, troubleshooting)?
See the [usage guide](../skills/bubble-buddy/references/usage.md) and the
[support skills](../skills/README.md).

## Contributing

This is a personal project with a small, curated scope, so pull requests aren't
actively sought. Bug reports and ideas via
[issues](https://github.com/ThousandOfWind/bubble-buddy/issues) are welcome.
`main` is protected — any change goes through a reviewed pull request.

After preparing the environment, run **both** required commands in separate
Python processes:

```bash
uv run --no-sync python -m unittest discover -s tests -p install_from_index_cases.py -v
uv run --no-sync python -m unittest discover -s tests -v
```

The first runs 26 standalone bootstrap cases; the second runs the 282
application/support cases at this revision. Default discovery intentionally
excludes the standalone case filename. CI gates both exits in the same
`unittest` job; an `OK` summary is insufficient if the process then crashes.
Do not import or wrap bootstrap cases inside the GUI test process. For a custom
prepared venv, use its explicit Python executable in both commands instead of
`uv run --no-sync python`.
