<div align="center">

<img src="https://raw.githubusercontent.com/ThousandOfWind/bubble-buddy/HEAD/assets/bb-logo.png" alt="Bubble Buddy logo" width="128" height="128" />

# 🫧 Bubble Buddy

**Talk to your computer. Bubble Buddy turns your voice into clean, ready-to-use text — right where you're working.**

[![Latest release](https://img.shields.io/github/v/release/ThousandOfWind/bubble-buddy?display_name=tag)](https://github.com/ThousandOfWind/bubble-buddy/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-blue)](#-get-started)
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
- 💻 **Offline mode** — local `mlx` / `faster-whisper` transcription, no network required after models are installed
- 🔌 **Extensible** — write context plugins to feed per-app context to the polisher

## 🚀 Get started

### Recommended — install the skill, let your agent do the rest

Download the Bubble Buddy skill; your AI agent installs and configures it for you.
Works with any [Agent Skills](https://agentskills.io)-compatible agent (GitHub
Copilot CLI, Claude Code, Codex, Cursor, Gemini CLI and
[60+ more](https://github.com/vercel-labs/skills#supported-agents)). It
auto-detects your OS and picks the right Windows or macOS build.

```bash
# 1. Add the skill (pick your agent + scope)
npx skills add ThousandOfWind/bubble-buddy
```

```text
# 2. In your agent (Copilot CLI: type "/"), trigger it:
/bubble-buddy install with Azure OpenAI for STT and polish, set my hotkey to F9,
prefer Chinese & English
```

`npx skills update` refreshes it later.

### Manual — click-to-run installer

Download the latest **Setup** from the
[Releases page](https://github.com/ThousandOfWind/bubble-buddy/releases/latest)
and run it — no Python needed. Configure it in the app's ⚙ Settings.

### From source (developers)

```bash
git clone https://github.com/ThousandOfWind/bubble-buddy.git
cd bubble-buddy
uv sync

uv run bubble-buddy doctor                       # check your setup
uv run bubble-buddy desktop --hotkey f9 --paste  # launch the overlay
```

Requires macOS or Windows and Python 3.10+. It runs on the **offline local
Whisper** backend by default (downloads the `small` model on first use). To
switch to **Azure OpenAI** or tweak anything, use the ⚙ Settings panel or edit a
`config.json` (copy [`config.example.json`](config.example.json)). See the
[configuration guide](docs/configuration.md) for every key, and the
[usage guide](skills/bubble-buddy/references/usage.md) for hotkeys, file
transcription and Copilot CLI integration.

## 📂 What's in this repo

| Path | What it is |
|---|---|
| [`skills/bubble-buddy/`](skills/bubble-buddy/) | The support skill — install, use & troubleshoot Bubble Buddy from your AI agent |
| [`src/`](src/) | The app source |
| [`docs/`](docs/README.md) | Developer docs — configuration, Azure backend, packaging, releasing & contributing |
| [`packaging/`](packaging/) | Installer / build scripts for Windows & macOS |

## 💛 Support the project

Bubble Buddy is a solo side project. If it saves you time, the best way to help
is to support it — see **[SUPPORT.md](SUPPORT.md)** for ways to donate. Thank you! ☕
