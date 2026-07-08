<div align="center">

<img src="https://raw.githubusercontent.com/ThousandOfWind/bubble-buddy/main/assets/bb-logo.png" alt="Bubble Buddy logo" width="128" height="128" />

# 🫧 Bubble Buddy

**对着电脑说话,Bubble Buddy 把你的语音变成干净、可直接使用的文字——就落在你正在工作的地方。**

[![Latest release](https://img.shields.io/github/v/release/ThousandOfWind/bubble-buddy?display_name=tag)](https://github.com/ThousandOfWind/bubble-buddy/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS-blue)](#-安装)
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

## 🚀 安装

### 面向用户 —— 点击即用安装包

从 [**Releases 页面**](https://github.com/ThousandOfWind/bubble-buddy/releases/latest)
下载最新的 **Setup.exe** 并运行,无需安装 Python。或者让
[支持技能(support skill)](skills/README.md) 以对话方式帮你安装和配置。

### 面向开发者 —— 从源码运行

```bash
git clone https://github.com/ThousandOfWind/bubble-buddy.git
cd bubble-buddy
uv sync
```

环境要求:macOS 或 Windows、Python 3.10+,以及首次下载 Whisper 模型时的网络连接。

## ⚡ 快速开始

```bash
# 检查你的环境
uv run copilot-voice-shell doctor

# 启动桌面悬浮窗 —— 按 F9,说话,它会帮你粘贴
uv run copilot-voice-shell desktop --hotkey f9 --paste
```

👉 更多热键、文件转写、Copilot CLI 集成以及全部命令,见
[**使用指南**](skills/bubble-buddy/references/usage.md)。

## 📖 文档

| 面向用户 | 面向开发者 |
|---|---|
| [使用指南](skills/bubble-buddy/references/usage.md) | [配置](docs/configuration.md) |
| [支持技能](skills/README.md) | [Azure OpenAI 后端](docs/azure.md) |
| | [上下文插件](docs/context-plugins.md) |
| | [打包安装程序](docs/packaging.md) |
| | [发布新版本](docs/releasing.md) |

> 说明:开发者文档目前为英文。

## 💛 支持这个项目

Bubble Buddy 是我一个人利用业余时间做的项目。如果它帮你省了时间,最好的支持方式就是
赞助它——赞助方式见 **[SUPPORT.md](SUPPORT.md)**。谢谢!☕

## 🤝 参与贡献

这是一个范围小、精心维护的个人项目,所以我并不主动征集 Pull Request。欢迎通过
[issues](https://github.com/ThousandOfWind/bubble-buddy/issues) 反馈 bug 或提想法。
`main` 分支受保护:任何改动都要经过评审过的 Pull Request。

运行测试:

```bash
uv run python -m unittest discover -s tests
```

## 兼容性说明

> 本项目原名 `copilot-voice-shell`,现更名为 **Bubble Buddy**;为保持兼容,Python 导入包
> 仍为 `copilot_voice_shell`,命令行命令与用户数据目录(`~/.copilot-voice-shell`)保持不变。
