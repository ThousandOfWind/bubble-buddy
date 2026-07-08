<div align="center">

<img src="https://raw.githubusercontent.com/ThousandOfWind/bubble-buddy/main/assets/bb-logo.png" alt="Bubble Buddy logo" width="128" height="128" />

# 🫧 Bubble Buddy

**Talk to your computer. Bubble Buddy turns your voice into clean, ready-to-use text — right where you're working.**

[![Latest release](https://img.shields.io/github/v/release/ThousandOfWind/bubble-buddy?display_name=tag)](https://github.com/ThousandOfWind/bubble-buddy/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-blue)](#installation)
[![Support](https://img.shields.io/badge/%F0%9F%92%9B-Support%20this%20project-db61a2)](SUPPORT.md)

**English** · [简体中文](README.zh-CN.md)

</div>

Bubble Buddy is a lightweight voice-dictation overlay for developer workflows. Hit
a hotkey, speak, and your words are transcribed, polished, and pasted into the app
you're in — a terminal, an editor, or a chat. It shines with **GitHub Copilot CLI**,
adapting the transcript to whatever you're currently doing.

## ✨ Features

- 🎙️ **One-key dictation** — global hotkey or a floating desktop overlay
- 🧹 **Smart polish** — cleans up filler and fixes wording, keeping mixed 中文/English intent
- 📋 **Drops text where you want it** — print, copy, paste, or paste-and-submit
- 🧠 **Context-aware** — adapts to your focused app (editor, Copilot CLI, chat, web)
- ☁️ **Azure OpenAI backend** — cloud transcription + LLM polish with your Azure sign-in (no API key stored)
- 💻 **Offline mode** — local `faster-whisper` transcription, no network required
- 🔌 **Extensible** — write context plugins to feed per-app context to the polisher

## 🚀 Installation

### For users — click-to-run installer

Download the latest **Setup.exe** from the
[**Releases page**](https://github.com/ThousandOfWind/bubble-buddy/releases/latest)
and run it. No Python required. Or let the
[support skills](skills/README.md) install and configure it for you conversationally.

### For developers — from source

```bash
git clone https://github.com/ThousandOfWind/bubble-buddy.git
cd bubble-buddy
uv sync
```

Requirements: macOS or Windows, Python 3.10+, and network access for the first
Whisper model download.

## ⚡ Quick start

```bash
# Check your setup
uv run copilot-voice-shell doctor

# Launch the desktop overlay — press F9, speak, and it pastes for you
uv run copilot-voice-shell desktop --hotkey f9 --paste
```

👉 See the [**usage guide**](skills/bubble-buddy/references/usage.md) for hotkeys, file transcription,
Copilot CLI integration, and every command.

## 📖 Documentation

| For users | For developers |
|---|---|
| [Usage guide](skills/bubble-buddy/references/usage.md) | [Configuration](docs/configuration.md) |
| [Support skills](skills/README.md) | [Azure OpenAI backend](docs/azure.md) |
| | [Context plugins](docs/context-plugins.md) |
| | [Packaging an installer](docs/packaging.md) |
| | [Releasing](docs/releasing.md) |

## 💛 Support the project

Bubble Buddy is a solo side project. If it saves you time, the best way to help
is to support it — see **[SUPPORT.md](SUPPORT.md)** for ways to donate. Thank you! ☕

## 🤝 Contributing

This is a personal project with a small, curated scope, so I'm not actively
seeking pull requests. Bug reports and ideas via
[issues](https://github.com/ThousandOfWind/bubble-buddy/issues) are appreciated.
`main` is protected: any change goes through a reviewed pull request.

Run the tests with:

```bash
uv run python -m unittest discover -s tests
```

## Compatibility note

> Formerly `copilot-voice-shell`. The product/repo is now **Bubble Buddy**; the
> Python import package is still `copilot_voice_shell` and the CLI command /
> user-data folder (`~/.copilot-voice-shell`) are unchanged for compatibility.
