from __future__ import annotations

import re
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .session_context import get_active_copilot_context


FILLER_PATTERNS = [
    r"\b呃\b",
    r"\b嗯\b",
    r"\b啊\b",
    r"\b额\b",
    r"\b诶\b",
    r"\b欸\b",
    r"\b那个\b",
    r"\b就是\b",
    r"\b其实\b",
    r"\b反正\b",
    r"\b怎么说呢\b",
    r"\b怎么讲\b",
    r"\b然后\b(?=\s*$)",
    r"\bOK\b",
    r"\bokay\b",
]

PROMPT_PREFIX_PATTERNS = [
    r"^\s*请执行下面的语音指令[:：]\s*",
    r"^\s*请基于当前\s*Copilot\s*会话上下文执行下面的语音指令[:：]\s*",
    r"^\s*指令[:：]\s*",
]

TERM_REPLACEMENTS = {
    "copilot": "Copilot",
    "copilot cli": "Copilot CLI",
    "github": "GitHub",
    "vscode": "VS Code",
    "vs code": "VS Code",
    "visual studio code": "VS Code",
    "cloud code": "Claude Code",
    "claude code": "Claude Code",
    "pull request": "PR",
    "pr": "PR",
    "api": "API",
    "mlx": "MLX",
    "whisper": "Whisper",
    "dashboard": "dashboard",
    "streaming": "streaming",
    "skill": "skill",
    "scale": "skill",
    "qwen": "Qwen",
    "q one": "Qwen",
    "dash board": "dashboard",
    "active copilot cli session": "active Copilot CLI session",
}

GLOSSARY = [
    "Copilot",
    "Copilot CLI",
    "active Copilot CLI session",
    "voice shell",
    "dashboard",
    "transcript",
    "rephrase",
    "summarize",
    "polish",
    "Qwen",
    "gemma3",
    "Ollama",
    "MLX",
    "Whisper",
    "large-v3-turbo",
    "streaming",
    "skill",
    "ASR",
    "Apple Silicon",
    "VS Code",
]

SCRIPT_PATTERNS = {
    "korean": r"[\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF]+",
    "thai": r"[\u0E00-\u0E7F]+",
    "japanese": r"[\u3040-\u30FF]+",
}


# The built-in polish categories, in display/priority order. This is the single
# source of truth for defaults; the effective categories are read from config
# (key ``polish_categories``) so users can add, remove, or edit them at runtime.
# Each category has: key, label (human name), color (accent hex), keywords (app
# name/bundle substrings that map to this category in auto mode), prompt.
# "copilot" is the general fallback (empty keywords -> matches nothing directly).
BUILTIN_CATEGORIES: list[dict] = [
    {
        "key": "copilot",
        "label": "通用 General",
        "color": "#6EA8FC",
        "keywords": [],
        "prompt": (
            "你是语音听写整理器。只输出整理后的用户原始指令，不要解释，不要编号，不要加前缀。\n"
            "任务：修正中英文 ASR 错误、规范技术词、去掉语气词和重复词，整理成更清楚但不改变意图的版本。\n"
            "请补全自然的中文/英文标点，尤其是句末标点；不要输出无标点长句。\n"
            "重要约束：不要总结成泛泛短句；不要删掉限定条件；不要把命令改成疑问句；不要添加用户没说的新需求。\n"
            "保留用户的中英混杂表达，不要翻译技术词，不要删掉不确定内容。\n"
        ),
    },
    {
        "key": "dev",
        "label": "开发 Dev",
        "color": "#57CC99",
        "keywords": [
            "code", "cursor", "windsurf", "iterm", "terminal", "kitty", "alacritty",
            "wezterm", "cmd", "powershell", "bash", "zsh", "intellij", "pycharm",
            "webstorm", "clion", "golang", "eclipse", "xcode", "sublime", "emacs",
            "vim", "visualstudio",
        ],
        "prompt": (
            "你是开发场景下的语音听写整理器。只输出整理后的开发指令、代码、类名、变量名或终端命令，不要解释，不要加前缀，不要加格式包裹。\n"
            "任务：修正技术词汇拼写（如 Git, Python, React 等），规范变量名、大小写和特殊字符。\n"
            "去除所有口语语气助词（如 呃、啊、就是、然后），保留最紧凑、最直接的命令或代码意图表达。\n"
            "保留用户原本的中英混杂开发习惯，不要强行进行整句翻译。句末可以省略标点，使其适合在终端或编辑器中直接输入。\n"
            "重要约束：不要添加用户没说的新需求。\n"
        ),
    },
    {
        "key": "im",
        "label": "即时通讯 IM",
        "color": "#FF8CC6",
        "keywords": [
            "wechat", "xinwechat", "tencent.xin", "lark", "feishu", "slack", "teams",
            "dingtalk", "ding", "telegram", "discord", "whatsapp", "zoom", "skype",
        ],
        "prompt": (
            "你是即时通讯（IM聊天）场景下的语音听写整理器。只输出整理后的聊天内容，不要解释，不要加前缀，不要任何AI寒暄。\n"
            "任务：将语音听写的口语整理为自然、连贯、流畅且口语化的聊天文本。去掉重复词和口吃错字。\n"
            "请补全亲和、自然的标点符号，保持轻松友好的对话感。完美支持并优化中英文混杂的说话习惯。\n"
            "重要约束：不要把聊天的语意改得过于生硬或正式。\n"
        ),
    },
    {
        "key": "notes",
        "label": "文档笔记 Notes",
        "color": "#B59CFA",
        "keywords": [
            "notion", "obsidian", "logseq", "typora", "siyuan", "bear", "onenote",
            "evernote",
        ],
        "prompt": (
            "你是文档和笔记场景下的语音听写整理器。只输出整理后的Markdown文本，不要解释，不要加前缀，不要任何说明。\n"
            "任务：整理文档或笔记。将口语化表达转化为逻辑清晰、排版精美的书面文本。\n"
            "自动美化排版：若检测到‘首先、第二点、第一、最后’等词，自动将它们转换为 Markdown 格式的有序或无序列表。合理划分布局与分段，补充合适的句末标点。\n"
            "重要约束：不要删减重要概念和细节。\n"
        ),
    },
    {
        "key": "email",
        "label": "邮件汇报 Email",
        "color": "#FFD166",
        "keywords": ["outlook", "gmail", "mail", "thunderbird"],
        "prompt": (
            "你是邮件汇报场景下的语音听写整理器。只输出整理后的邮件或工作汇报文本，不要解释，不要加前缀。\n"
            "任务：将语音口语整理润色为逻辑严密、得体、礼貌、格式规范的专业商务邮件或工作汇报文风。\n"
            "修正口语大白话，使措辞更加职业、客观、礼貌。保证段落结构清晰，标点符号规范严谨。\n"
            "重要约束：不要编造发件人或收件人的具体虚构姓名，仅润色用户表达的内容主体。\n"
        ),
    },
    {
        "key": "browser",
        "label": "浏览器检索 Browser",
        "color": "#78D6FA",
        "keywords": ["chrome", "safari", "edge", "arc", "firefox", "opera", "vivaldi"],
        "prompt": (
            "你是浏览器检索和搜索场景下的语音听写整理器。只输出整理后的搜索关键词、Query 或提问句，不要解释，不要加前缀，不要有任何标点包裹。\n"
            "任务：将长句缩减、提炼，过滤掉所有无用的修饰词、口头禅和客套话，提炼成高精度的检索关键词（Query）。\n"
            "例如：将‘我想查一下怎么用 python 处理 json’整理为‘python handle json’；将‘帮我百度一下今天的天气如何’整理为‘今天天气’。\n"
            "重要约束：只需输出最终的查询词，句末不要带句号或其他标点。\n"
        ),
    },
]

# Non-category UI options that still need a color/label.
_OFF_COLOR = "#8892A6"
_EXTRA_LABELS = {"off": "不润色 Off", "auto": "自动 Auto"}

# Legacy flat dicts derived from BUILTIN_CATEGORIES (kept for backward compatibility
# with any code/tests referencing them directly; effective lookups go through config).
POLISH_PROMPTS = {c["key"]: c["prompt"] for c in BUILTIN_CATEGORIES}
POLISH_MODE_COLORS = {c["key"]: c["color"] for c in BUILTIN_CATEGORIES}
POLISH_MODE_COLORS["off"] = _OFF_COLOR
POLISH_MODE_LABELS = {c["key"]: c["label"] for c in BUILTIN_CATEGORIES}
POLISH_MODE_LABELS.update(_EXTRA_LABELS)


def _effective_categories() -> list[dict]:
    """The active polish categories: the user's ``polish_categories`` from config
    if present and valid, otherwise the built-in defaults. Never raises."""
    try:
        from . import config as _config

        cats = _config.load_config().get("polish_categories")
        if isinstance(cats, list):
            out = [c for c in cats if isinstance(c, dict) and c.get("key")]
            if out:
                return out
    except Exception:
        pass
    return [dict(c) for c in BUILTIN_CATEGORIES]


def _category_for(mode: str) -> dict | None:
    for cat in _effective_categories():
        if cat.get("key") == mode:
            return cat
    return None


def polish_mode_color(mode: str) -> str:
    if mode == "off":
        return _OFF_COLOR
    cat = _category_for(mode)
    if cat and cat.get("color"):
        return str(cat["color"])
    return POLISH_MODE_COLORS.get(mode, POLISH_MODE_COLORS["copilot"])


def polish_mode_label(mode: str) -> str:
    if mode in _EXTRA_LABELS:
        return _EXTRA_LABELS[mode]
    cat = _category_for(mode)
    if cat and cat.get("label"):
        return str(cat["label"])
    return POLISH_MODE_LABELS.get(mode, mode)


def get_polish_prompt(mode: str) -> str:
    """The effective prompt for ``mode``: from the matching config category, or a
    legacy ``polish_prompts.<mode>`` override, otherwise the built-in default. Lets
    users tailor each scenario's polish prompt without editing the source."""
    cat = _category_for(mode)
    if cat:
        prompt = (cat.get("prompt") or "").strip()
        if prompt:
            return prompt
    try:
        from . import config as _config

        overrides = _config.load_config().get("polish_prompts") or {}
        custom = (overrides.get(mode) or "").strip()
        if custom:
            return custom
    except Exception:
        pass
    return POLISH_PROMPTS.get(mode, POLISH_PROMPTS["copilot"])


def describe_polish_context(mode: str, context: str = "") -> str:
    """A human-readable summary of the extra instructions/context the active app's
    category dynamically injects into the polish prompt. Shown in the expanded UI
    so the user can see exactly what context is active."""
    if mode == "off":
        return "润色已关闭，不注入任何场景指令。"
    base = get_polish_prompt(mode).strip()
    parts = [base]
    if GLOSSARY:
        parts.append(f"技术词表：{', '.join(GLOSSARY)}")
    if context:
        parts.append(f"会话上下文：{context}")
    return "\n\n".join(parts)


def map_app_to_polish_mode(app_name: str, bundle_id: str = "") -> str:
    """Map an app name or bundle ID to a polish category key by matching each
    category's ``keywords`` (config-driven). Falls back to 'copilot' (general)."""
    name_lower = (app_name or "").lower()
    bundle_lower = (bundle_id or "").lower()

    for cat in _effective_categories():
        if cat.get("key") == "copilot":
            continue
        keywords = cat.get("keywords") or []
        for kw in keywords:
            kw = str(kw).strip().lower()
            if not kw:
                continue
            if kw in name_lower or kw in bundle_lower:
                return str(cat["key"])

    return "copilot"


def resolve_polish_mode(mode: str, app_name: str = "", bundle_id: str = "") -> str:
    """Return the effective polish mode, resolving 'auto' via the active app.

    Shared by polish_text (to pick the prompt) and the UI (to show the user which
    style is active), so both always agree."""
    if mode == "auto":
        if app_name or bundle_id:
            return map_app_to_polish_mode(app_name, bundle_id)
        return "copilot"
    return mode


def polish_text(
    text: str,
    mode: str,
    context_file: Path | None = None,
    *,
    language_preference: str = "zh-en",
    blocked_scripts: set[str] | None = None,
    engine: str = "rules",
    ollama_model: str = "qwen3:latest",
    session_context: bool = False,
    target_app_name: str | None = None,
    target_app_bundle_id: str | None = None,
    live_context: str = "",
) -> str:
    if mode not in ("off", "auto") and _category_for(mode) is None:
        # Unknown category (e.g. one removed from config, or a stale value): fall
        # back to the general mode rather than raising and losing the transcript.
        mode = "copilot"

    if mode == "off":
        return cleanup_dictation(text, language_preference=language_preference, blocked_scripts=blocked_scripts)

    resolved_mode = mode
    if mode == "auto":
        app_name = target_app_name or ""
        app_bundle = target_app_bundle_id or ""
        if not app_name:
            try:
                from .cli import get_frontmost_app_info
                app_target = get_frontmost_app_info()
                app_name = app_target.name or ""
                app_bundle = app_target.bundle_id or ""
            except BaseException:
                app_name = ""
                app_bundle = ""

        if app_name or app_bundle:
            resolved_mode = map_app_to_polish_mode(app_name, app_bundle)
            print(f"[polish] Auto-detected app '{app_name}' ({app_bundle}) -> Mapping to '{resolved_mode}' mode")
        else:
            resolved_mode = "copilot"
            print("[polish] Auto detection returned empty. Defaulting to 'copilot' mode")

    cleaned = cleanup_dictation(text, language_preference=language_preference, blocked_scripts=blocked_scripts)
    context = read_context(context_file)
    if session_context and not context:
        context = get_active_copilot_context()
    # Live focus context (window title / editor / terminal / chat text captured at
    # record time) takes precedence — it's what the user is actually looking at.
    if live_context and live_context.strip():
        context = f"{live_context.strip()}\n\n{context}".strip() if context else live_context.strip()
    if not cleaned:
        return cleaned

    if engine == "ollama":
        polished_result = polish_with_ollama(cleaned, context, ollama_model, resolved_mode)
        if resolved_mode in ("dev", "browser"):
            return polished_result
        return ensure_sentence_punctuation(polished_result)
    if engine == "azure":
        from . import azure_client

        polished_result = azure_client.polish(
            cleaned,
            context=context,
            glossary=GLOSSARY,
            language_preference=language_preference,
            mode_prompt=get_polish_prompt(resolved_mode),
        )
        if resolved_mode in ("dev", "browser"):
            return polished_result
        return ensure_sentence_punctuation(polished_result)
    if engine != "rules":
        raise ValueError(f"Unsupported polish engine: {engine}")

    if resolved_mode in ("dev", "browser"):
        return cleaned

    if context:
        return f"{ensure_sentence_punctuation(cleaned)}\n\n[会话上下文摘要：{context}]"
    return ensure_sentence_punctuation(cleaned)


def polish_with_ollama(text: str, context: str, model: str, mode: str = "copilot") -> str:
    context_line = f"\n当前会话摘要：{context}" if context else ""
    base_prompt = get_polish_prompt(mode)
    prompt = (
        f"{base_prompt}\n"
        f"优先参考这些技术词：{', '.join(GLOSSARY)}。\n"
        f"{context_line}\n"
        f"输入：{text}"
    )
    return polish_with_ollama_api(prompt, model) or polish_with_ollama_cli(prompt, model) or text


def polish_with_ollama_api(prompt: str, model: str) -> str:
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.1, "num_predict": 96},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return ""
    return strip_ollama_noise(str(payload.get("response", ""))).strip()


def polish_with_ollama_cli(prompt: str, model: str) -> str:
    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""
    return strip_ollama_noise(result.stdout).strip()


def strip_ollama_noise(output: str) -> str:
    cleaned = output.strip()
    cleaned = re.sub(r"(?s)^.*?done thinking\.\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?s)<think>.*?</think>\s*", "", cleaned, flags=re.IGNORECASE)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return ""
    # Prefer the last non-empty line because some local models emit reasoning first.
    return lines[-1]


def ensure_sentence_punctuation(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return cleaned
    if re.search(r"[。！？.!?）)\]}]$", cleaned):
        return cleaned
    if re.search(r"(吗|么|是不是|能不能|可不可以|有没有|为何|为什么)$", cleaned) or re.search(
        r"(能不能|可不可以|有没有|是否|是不是)", cleaned
    ):
        return cleaned + "？"
    return cleaned + "。"


def cleanup_dictation(
    text: str,
    *,
    language_preference: str = "zh-en",
    blocked_scripts: set[str] | None = None,
) -> str:
    cleaned = text.strip()
    cleaned = remove_prompt_prefixes(cleaned)
    blocked = blocked_scripts or default_blocked_scripts(language_preference)
    cleaned = remove_blocked_scripts(cleaned, blocked)
    for pattern in FILLER_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = normalize_terms(cleaned)
    cleaned = normalize_spacing(cleaned)
    cleaned = reduce_repetition(cleaned)
    return cleaned





def remove_prompt_prefixes(text: str) -> str:
    cleaned = text
    for pattern in PROMPT_PREFIX_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def default_blocked_scripts(language_preference: str) -> set[str]:
    return set()


def remove_blocked_scripts(text: str, blocked_scripts: set[str]) -> str:
    cleaned = text
    for script in blocked_scripts:
        pattern = SCRIPT_PATTERNS.get(script)
        if pattern:
            cleaned = re.sub(pattern, "", cleaned)
    return cleaned


def normalize_terms(text: str) -> str:
    updated = text
    for source, target in sorted(TERM_REPLACEMENTS.items(), key=lambda item: len(item[0]), reverse=True):
        updated = re.sub(rf"(?<![A-Za-z]){re.escape(source)}(?![A-Za-z])", target, updated, flags=re.IGNORECASE)
    return updated


def normalize_spacing(text: str) -> str:
    text = re.sub(r"\s+([，。！？；：,.!?;:])", r"\1", text)
    text = re.sub(r"([（([{])\s+", r"\1", text)
    text = re.sub(r"\s+([）)\]}])", r"\1", text)
    return text.strip()


def reduce_repetition(text: str) -> str:
    cleaned = text
    cleaned = re.sub(r"([，。！？；：,.!?;:])\1+", r"\1", cleaned)

    # Reduce repeated Latin words: "test test" -> "test".
    cleaned = re.sub(r"\b([A-Za-z][A-Za-z0-9_-]{1,})\b(?:\s+\1\b)+", r"\1", cleaned, flags=re.IGNORECASE)

    # Reduce repeated Chinese phrases of 2-8 chars: "默认打开默认打开" -> "默认打开".
    cleaned = re.sub(r"([\u4e00-\u9fff]{2,8})(?:\1)+", r"\1", cleaned)

    # Reduce repeated Chinese phrase separated by light punctuation/space.
    cleaned = re.sub(r"([\u4e00-\u9fff]{2,8})(?:[，,、\s]+\1)+", r"\1", cleaned)

    # Reduce stuttered single Chinese chars only when repeated 3+ times: "你你你" -> "你".
    cleaned = re.sub(r"([\u4e00-\u9fff])\1{2,}", r"\1", cleaned)

    return re.sub(r"\s+", " ", cleaned).strip()


def read_context(context_file: Path | None) -> str:
    if context_file is None or not context_file.exists():
        return ""
    content = context_file.read_text(encoding="utf-8", errors="replace").strip()
    content = re.sub(r"\s+", " ", content)
    return content[:800]
