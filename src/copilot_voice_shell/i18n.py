"""Lightweight internationalization (i18n) for the desktop overlay.

A single flat catalog maps a stable key to per-language strings. The UI language
is resolved once at startup from ``config.ui_language`` (``auto``|``zh``|``en``);
``auto`` falls back to the OS locale. Call :func:`t` to look up a localized string
and :func:`set_language` to switch at runtime (the settings panel does this).

Keep this module dependency-free (no Qt) so it can be imported anywhere.
"""
from __future__ import annotations

import locale
from typing import Any

SUPPORTED = ("zh", "en")
DEFAULT_LANG = "zh"

# Flat catalog: key -> {"zh": ..., "en": ...}. Strings may contain ``{name}``
# placeholders filled via str.format in :func:`t`.
STRINGS: dict[str, dict[str, str]] = {
    # ---- window / status --------------------------------------------------
    "window.title": {"zh": "Bubble Buddy", "en": "Bubble Buddy"},
    "status.ready": {"zh": "就绪。", "en": "Ready."},
    "status.recording": {"zh": "录音中…", "en": "Recording..."},
    "status.finishing": {"zh": "收尾中…", "en": "Finishing…"},
    "status.done": {"zh": "完成。", "en": "Done."},
    "status.no_speech": {"zh": "未捕获到语音。", "en": "No speech captured."},
    "status.copied_clipboard": {"zh": "已复制到剪贴板。", "en": "Copied to clipboard."},
    "status.copy_failed": {"zh": "剪贴板复制失败：{error}", "en": "Clipboard copy failed: {error}"},
    "status.start_failed": {"zh": "启动失败：{error}", "en": "Start failed: {error}"},
    "status.stop_failed": {"zh": "停止失败：{error}", "en": "Stop failed: {error}"},
    "status.transcribing": {"zh": "正在转写 {name}{app}…", "en": "Transcribing {name}{app}..."},
    "status.streaming_realtime": {"zh": "实时转写中…{status}", "en": "Streaming (realtime)…{status}"},
    "status.polishing": {"zh": "润色中…{app}", "en": "Polishing…{app}"},
    "label.hotkey": {"zh": "热键：{hotkey}", "en": "Hotkey: {hotkey}"},
    "label.app_unknown": {"zh": "未识别应用", "en": "No app detected"},
    # ---- toolbar buttons / tooltips --------------------------------------
    "btn.start.tip": {"zh": "开始录音", "en": "Start recording"},
    "btn.stop.tip": {"zh": "停止录音", "en": "Stop recording"},
    "btn.shrink.tip": {"zh": "收起为小球", "en": "Shrink to orb"},
    "btn.quit.tip": {"zh": "退出", "en": "Quit"},
    "btn.relaunch.tip": {"zh": "重新启动", "en": "Relaunch"},
    "btn.copy_raw.tip": {"zh": "复制原始转写", "en": "Copy raw transcript"},
    "btn.copy_polished.tip": {"zh": "复制润色文本", "en": "Copy polished text"},
    "btn.copy": {"zh": "复制", "en": "Copy"},
    "btn.save": {"zh": "保存", "en": "Save"},
    # ---- section labels ---------------------------------------------------
    "label.raw_transcript": {"zh": "原始转写", "en": "Raw Transcript"},
    "label.active_context": {"zh": "当前上下文", "en": "Active Context"},
    "label.polished": {"zh": "润色结果", "en": "Polished"},
    "label.status_error": {"zh": "状态 / 错误", "en": "Status / Error"},
    "ph.transcript": {"zh": "等待语音…", "en": "Waiting for speech…"},
    "ph.context": {"zh": "尚未检测到应用上下文。", "en": "No app context detected yet."},
    "ph.polished": {"zh": "等待润色文本…", "en": "Waiting for polished text…"},
    "toggle.settings": {"zh": "⚙ 设置", "en": "⚙ Settings"},
    "toggle.history": {"zh": "🕘 历史", "en": "🕘 History"},
    "label.history_empty": {"zh": "还没有听写记录。", "en": "No dictations yet."},
    "msg.hotkey_help": {
        "zh": "如果热键无响应，请点击开始录音，并确认终端或 VS Code 已开启输入监控权限。",
        "en": "If the hotkey does not respond, click Start Recording and ensure Input Monitoring is enabled for your terminal or VS Code.",
    },
    # ---- bubbles / greeting ----------------------------------------------
    "bubble.already_running": {"zh": "Hi 👋 我已经在运行啦", "en": "Hi 👋 I'm already running"},
    "bubble.greeting": {
        "zh": "嗨，我是 BB 👋 按 {hotkey} 开始说话",
        "en": "Hi, I'm BB 👋 press {hotkey} to talk",
    },
    # ---- sign in / azure --------------------------------------------------
    "btn.signin": {"zh": "🔑 登录 Azure", "en": "🔑 Sign in to Azure"},
    "btn.signin.tip": {
        "zh": "使用浏览器登录 Azure（无需安装 Azure CLI）",
        "en": "Sign in to Azure via browser (no Azure CLI needed)",
    },
    "btn.signin_retry": {"zh": "🔑 登录 Azure（重试）", "en": "🔑 Sign in to Azure (retry)"},
    "btn.signin_opening": {"zh": "正在打开浏览器登录…", "en": "Opening browser to sign in…"},
    "msg.signin_browser": {
        "zh": "请在弹出的浏览器中完成 Azure 登录…",
        "en": "Complete the Azure sign-in in the browser window…",
    },
    "msg.signed_in": {"zh": "已登录 Azure{acct}。", "en": "Signed in to Azure{acct}."},
    "msg.not_signed_in": {
        "zh": "未登录 Azure：点击下方『登录 Azure』即可开始。",
        "en": "Not signed in to Azure: click “Sign in to Azure” below to start.",
    },
    "msg.signin_failed": {"zh": "Azure 登录失败：{message}", "en": "Azure sign-in failed: {message}"},
    "signin.hint_suffix": {"zh": "（{acct}）", "en": " ({acct})"},
    # ---- model download ---------------------------------------------------
    "btn.download_model": {"zh": "⬇ 下载所选本地模型", "en": "⬇ Download selected local model"},
    "btn.downloading_model": {"zh": "⬇ 正在下载 {name}…", "en": "⬇ Downloading {name}…"},
    "msg.pick_model_first": {
        "zh": "请先选择或输入一个本地模型名称。",
        "en": "Please select or type a local model name first.",
    },
    "msg.downloading_model": {
        "zh": "正在下载模型 {name}（首次较慢，请稍候）…",
        "en": "Downloading model {name} (first time is slow, please wait)…",
    },
    "msg.model_ready": {"zh": "模型 {name} 已就绪（{path}）。", "en": "Model {name} is ready ({path})."},
    "msg.model_failed": {"zh": "模型下载失败：{message}", "en": "Model download failed: {message}"},
    "msg.model_no_local_engine": {
        "zh": "此安装包为 Azure 精简版，未内置本地 Whisper 引擎，无法下载模型。请改用完整版（含离线 Whisper）。",
        "en": "This build is the lean Azure edition without a bundled local Whisper engine, "
              "so models can't be downloaded. Please use the Full edition (with offline Whisper).",
    },
    "msg.local_engine_missing": {
        "zh": "此安装包为 Azure 精简版，未内置本地 Whisper 引擎。请在设置中使用 azure 后端，或用 CVS_INCLUDE_LOCAL=1 重新打包。",
        "en": "This build is the lean Azure edition without a bundled local Whisper engine. "
              "Use the azure backend in Settings, or repackage with CVS_INCLUDE_LOCAL=1.",
    },
    # ---- settings save / copy --------------------------------------------
    "msg.settings_saved": {"zh": "设置已保存到 {name}。", "en": "Settings saved to {name}."},
    "msg.settings_save_failed": {"zh": "保存设置失败：{error}", "en": "Save settings failed: {error}"},
    "msg.field_empty": {"zh": "{label} 为空，无内容可复制。", "en": "{label} is empty; nothing to copy."},
    "msg.copied_field": {"zh": "已复制{label}到剪贴板。", "en": "Copied {label} to clipboard."},
    "msg.copied_history": {"zh": "已复制历史记录到剪贴板。", "en": "Copied history item to clipboard."},
    # ---- settings sections -----------------------------------------------
    "settings.section.general": {"zh": "常规", "en": "General"},
    "settings.section.transcription": {"zh": "转写", "en": "Transcription"},
    "settings.section.polish": {"zh": "润色", "en": "Polish"},
    "settings.section.output": {"zh": "输出", "en": "Output"},
    "settings.section.azure": {"zh": "线上模型 Azure", "en": "Cloud model (Azure)"},
    "settings.section.categories": {"zh": "分类管理", "en": "Categories"},
    # ---- settings fields --------------------------------------------------
    "settings.field.ui_language": {"zh": "界面语言", "en": "Interface language"},
    "settings.field.language_preference": {"zh": "语言偏好", "en": "Language preference"},
    "settings.field.language": {"zh": "语言提示", "en": "Language hint"},
    "settings.field.hotkey": {"zh": "热键", "en": "Hotkey"},
    "settings.field.input_device": {"zh": "输入设备", "en": "Input device"},
    "settings.field.start_collapsed": {"zh": "启动时收起为小球", "en": "Start collapsed as orb"},
    "settings.field.max_record_seconds": {
        "zh": "最大收听秒数 (0=不限)", "en": "Max listen seconds (0 = no limit)",
    },
    "settings.field.backend": {"zh": "后端", "en": "Backend"},
    "settings.field.model": {"zh": "本地 Whisper 模型", "en": "Local Whisper model"},
    "settings.field.download_model": {"zh": "⬇ 下载所选本地模型", "en": "⬇ Download selected local model"},
    "settings.field.hf_endpoint": {"zh": "HF endpoint", "en": "HF endpoint"},
    "settings.field.mlx_model": {"zh": "MLX 模型", "en": "MLX model"},
    "settings.field.polish": {"zh": "润色", "en": "Polish"},
    "settings.field.polish_engine": {"zh": "润色引擎", "en": "Polish engine"},
    "settings.field.ollama_model": {"zh": "Ollama 模型", "en": "Ollama model"},
    "settings.field.copy_to_clipboard": {"zh": "复制到剪贴板", "en": "Copy to clipboard"},
    "settings.field.paste_to_active_app": {"zh": "复制到光标", "en": "Paste at cursor"},
    "settings.field.submit_to_active_app": {"zh": "粘贴后回车提交", "en": "Press Enter after paste"},
    "settings.field.azure.endpoint": {"zh": "Endpoint", "en": "Endpoint"},
    "settings.field.azure.api_version": {"zh": "API version", "en": "API version"},
    "settings.field.azure.auth": {"zh": "Auth", "en": "Auth"},
    "settings.field.azure.api_key": {"zh": "API key (api_key 模式)", "en": "API key (api_key mode)"},
    "settings.field.azure.transcribe_deployment": {"zh": "转写部署", "en": "Transcribe deployment"},
    "settings.field.azure.transcribe_mode": {"zh": "转写模式 Streaming", "en": "Transcribe mode (streaming)"},
    "settings.field.azure.realtime_api_version": {"zh": "Realtime API version", "en": "Realtime API version"},
    "settings.field.azure.chat_deployment": {"zh": "对话部署", "en": "Chat deployment"},
    # ---- categories editor ------------------------------------------------
    "categories.note": {
        "zh": (
            "为每个场景（分类）自定义：显示名、颜色、匹配的 App 关键词（逗号分隔，"
            "auto 模式据此识别当前应用）、以及润色 Prompt。可新增或删除分类。\n"
            "Prompt 仅对 Ollama / Azure 润色引擎生效；关键词与颜色对所有引擎生效。"
        ),
        "en": (
            "Customize each scenario (category): display name, color, matching app "
            "keywords (comma-separated; auto mode uses them to detect the current app), "
            "and the polish prompt. You can add or remove categories.\n"
            "The prompt only applies to the Ollama / Azure polish engines; keywords and "
            "color apply to all engines."
        ),
    },
    "categories.add": {"zh": "➕ 新增分类", "en": "➕ Add category"},
    "categories.remove": {"zh": "🗑 删除此分类", "en": "🗑 Remove"},
    "categories.field.label": {"zh": "显示名", "en": "Display name"},
    "categories.field.color": {"zh": "颜色", "en": "Color"},
    "categories.field.keywords": {"zh": "App 关键词", "en": "App keywords"},
    "categories.field.prompt": {"zh": "润色 Prompt", "en": "Polish prompt"},
    # ---- polish extra labels ---------------------------------------------
    "polish.off": {"zh": "不润色 Off", "en": "Off"},
    "polish.auto": {"zh": "自动 Auto", "en": "Auto"},
    # ---- focus sub-kinds --------------------------------------------------
    "subkind.terminal": {"zh": "终端", "en": "Terminal"},
    "subkind.editor": {"zh": "编辑器", "en": "Editor"},
    "subkind.chat": {"zh": "会话", "en": "Chat"},
    "subkind.browser": {"zh": "网页", "en": "Web"},
    "subkind.document": {"zh": "文档", "en": "Document"},
    # ---- active context panel --------------------------------------------
    "ctx.session": {"zh": "当前会话", "en": "Current session"},
    "ctx.session_unnamed": {"zh": "(未命名会话)", "en": "(unnamed session)"},
    "ctx.window_title": {"zh": "窗口标题", "en": "Window title"},
    "ctx.focus_area": {"zh": "焦点区域", "en": "Focus area"},
    "ctx.focus_content": {"zh": "焦点内容", "en": "Focus content"},
    "ctx.default_label": {"zh": "上下文", "en": "Context"},
    "ctx.unknown_app": {"zh": "未识别应用", "en": "Unknown app"},
}

_lang: str = DEFAULT_LANG


def _detect_os_language() -> str:
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        loc = ""
    if not loc:
        try:
            loc = locale.getdefaultlocale()[0] or ""  # noqa: DEP004 (fallback only)
        except Exception:
            loc = ""
    return "zh" if loc.lower().startswith("zh") else "en"


def resolve_language(pref: str | None) -> str:
    """Map a config preference (``auto``/``zh``/``en``/None) to a supported code."""
    pref = (pref or "auto").strip().lower()
    if pref in SUPPORTED:
        return pref
    return _detect_os_language()


def set_language(pref: str | None) -> str:
    """Set the active UI language from a preference; returns the resolved code."""
    global _lang
    _lang = resolve_language(pref)
    return _lang


def current_language() -> str:
    return _lang


def t(key: str, /, **fmt: Any) -> str:
    """Localized string for ``key`` in the active language. Falls back to the other
    language, then to the key itself. ``fmt`` values are substituted via format."""
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(_lang) or entry.get("en") or entry.get("zh") or key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text
