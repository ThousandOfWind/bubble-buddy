from __future__ import annotations

import re
import subprocess
from pathlib import Path


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
}

SCRIPT_PATTERNS = {
    "korean": r"[\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF]+",
    "thai": r"[\u0E00-\u0E7F]+",
    "japanese": r"[\u3040-\u30FF]+",
}


def polish_text(
    text: str,
    mode: str,
    context_file: Path | None = None,
    *,
    language_preference: str = "zh-en",
    blocked_scripts: set[str] | None = None,
    engine: str = "rules",
    ollama_model: str = "gemma3:latest",
) -> str:
    if mode == "off":
        return cleanup_dictation(text, language_preference=language_preference, blocked_scripts=blocked_scripts)
    if mode != "copilot":
        raise ValueError(f"Unsupported polish mode: {mode}")

    cleaned = cleanup_dictation(text, language_preference=language_preference, blocked_scripts=blocked_scripts)
    context = read_context(context_file)
    if not cleaned:
        return cleaned

    if engine == "ollama":
        return polish_with_ollama(cleaned, context, ollama_model)
    if engine != "rules":
        raise ValueError(f"Unsupported polish engine: {engine}")

    if context:
        return f"{cleaned}\n\n[会话上下文摘要：{context}]"
    return cleaned


def polish_with_ollama(text: str, context: str, model: str) -> str:
    context_line = f"\n当前会话摘要：{context}" if context else ""
    prompt = (
        "你是语音听写整理器。只输出整理后的用户原始指令，不要解释，不要编号，不要加前缀。\n"
        "任务：修正中英文 ASR 错误、规范技术词、去掉语气词和重复词，重新组织成更清楚但不改变意图的版本。\n"
        "保留用户的中英混杂表达，不要翻译技术词，不要删掉不确定内容。\n"
        f"{context_line}\n"
        f"输入：{text}"
    )
    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return text
    return strip_ollama_noise(result.stdout).strip() or text


def strip_ollama_noise(output: str) -> str:
    cleaned = output.strip()
    cleaned = re.sub(r"(?s)^.*?done thinking\.\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?s)<think>.*?</think>\s*", "", cleaned, flags=re.IGNORECASE)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return ""
    # Prefer the last non-empty line because some local models emit reasoning first.
    return lines[-1]


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
