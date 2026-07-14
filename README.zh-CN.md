<div align="center">

<img src="assets/bb-logo.png" alt="Bubble Buddy logo" width="128" height="128" />

# 🫧 Bubble Buddy

**对着电脑说话,Bubble Buddy 把你的语音变成干净、可直接使用的文字——就落在你正在工作的地方。**

[![Latest release](https://img.shields.io/github/v/release/ThousandOfWind/bubble-buddy?display_name=tag)](https://github.com/ThousandOfWind/bubble-buddy/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-blue)](#-开始使用)
[![Support](https://img.shields.io/badge/%F0%9F%92%9B-Support%20this%20project-db61a2)](SUPPORT.md)

[English](README.md) · **简体中文**

</div>

Bubble Buddy 是一个面向开发者工作流的轻量语音听写悬浮窗。按下热键、开口说话,你的话会被
转写、润色,并粘贴进你当前所在的应用——终端、编辑器或聊天窗口。它与 **GitHub Copilot CLI**
配合尤其出色,会根据你正在做的事情自动调整转写结果。

## ✨ 功能特性

- 🎙️ **一键听写** —— 全局热键,或一个浮动的桌面悬浮窗
- 🧹 **智能润色** —— 清除口水词、修正措辞,并保留中英文混合表达
- 📋 **文字随你落点** —— 打印、复制、粘贴,或粘贴后自动提交
- 🧠 **上下文感知** —— 根据当前聚焦的应用自动适配(编辑器、Copilot CLI、聊天、网页)
- ☁️ **Azure OpenAI 后端** —— 用你的 Azure 登录做云端转写 + 大模型润色(不存储 API key)
- 💻 **离线模式** —— 本地 `faster-whisper` 转写,无需联网
- 🔌 **可扩展** —— 编写上下文插件,为润色器提供各应用的专属上下文

## 🚀 开始使用

### 推荐 —— 装上技能,让助手替你搞定

下载 Bubble Buddy 技能,你的 AI 助手就会帮你安装并配置。兼容所有
[Agent Skills](https://agentskills.io) 格式的助手(GitHub Copilot CLI、Claude
Code、Codex、Cursor、Gemini CLI 等 [60+ 种](https://github.com/vercel-labs/skills#supported-agents)),
自动识别系统,选对 Windows 或 macOS 版本。

```bash
# 1. 添加技能(选择助手 + 安装范围)
npx skills add ThousandOfWind/bubble-buddy
```

```text
# 2. 在助手里触发(Copilot CLI:输入 "/"):
/bubble-buddy 用 Azure OpenAI 做语音转写(STT)和润色,热键设成 F9,
中英文混说
```

之后用 `npx skills update` 更新。

### 手动 —— 点击即用安装包

从 [Releases 页面](https://github.com/ThousandOfWind/bubble-buddy/releases/latest)
下载最新的 **Setup** 运行,无需 Python。装好后在应用的 **⚙ 设置**里配置。

### 从源码运行(开发者)

```bash
git clone https://github.com/ThousandOfWind/bubble-buddy.git
cd bubble-buddy
uv sync

uv run bubble-buddy doctor                       # 检查你的环境
uv run bubble-buddy desktop --hotkey f9 --paste  # 启动悬浮窗
```

环境要求:macOS 或 Windows、Python 3.10+。默认走**离线本地 Whisper** 后端(`small`
模型,首次使用自动下载)。想切换到 **Azure OpenAI** 或调整任何设置,用 **⚙ 设置**面板,
或编辑 `config.json`(可复制 [`config.example.json`](config.example.json))。全部配置项见
[配置文档](docs/configuration.md);热键、文件转写、Copilot CLI 集成等见
[使用指南](skills/bubble-buddy/references/usage.md)。

## 📂 仓库结构

| 路径 | 是什么 |
|---|---|
| [`skills/bubble-buddy/`](skills/bubble-buddy/) | 支持技能 —— 在你的 AI 助手里安装、使用、排障 Bubble Buddy |
| [`src/`](src/) | 应用源码 |
| [`docs/`](docs/README.md) | 开发者文档 —— 配置、Azure 后端、打包、发布与参与贡献 |
| [`packaging/`](packaging/) | Windows / macOS 的安装包与构建脚本 |

## 💛 支持这个项目

Bubble Buddy 是我一个人利用业余时间做的项目。如果它帮你省了时间,最好的支持方式就是
赞助它——赞助方式见 **[SUPPORT.md](SUPPORT.md)**。谢谢!☕
