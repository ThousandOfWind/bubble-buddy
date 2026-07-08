# Developer documentation

Detailed guides for building, configuring and extending Bubble Buddy.

| Guide | What it covers |
|---|---|
| [Configuration](configuration.md) | `config.json`, load order, the Settings panel, recording limit |
| [Azure OpenAI backend](azure.md) | Cloud transcription + LLM polishing, auth, transcribe modes |
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

Run the tests with:

```bash
uv run python -m unittest discover -s tests
```
