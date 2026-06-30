from __future__ import annotations

import re
from pathlib import Path


FILLER_PATTERNS = [
    r"\b呃\b",
    r"\b嗯\b",
    r"\b啊\b",
    r"\b额\b",
    r"\b那个\b",
    r"\b就是\b",
    r"\b然后\b(?=\s*$)",
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
    "streaming": "streaming",
    "skill": "skill",
    "scale": "skill",
}


def polish_text(text: str, mode: str, context_file: Path | None = None) -> str:
    if mode == "off":
        return text.strip()
    if mode != "copilot":
        raise ValueError(f"Unsupported polish mode: {mode}")

    cleaned = cleanup_dictation(text)
    context = read_context(context_file)
    if not cleaned:
        return cleaned

    if context:
        return (
            "请基于当前 Copilot 会话上下文执行下面的语音指令。\n\n"
            f"上下文摘要：{context}\n\n"
            f"指令：{cleaned}"
        )
    return f"请执行下面的语音指令：{cleaned}"


def cleanup_dictation(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"[า]{2,}", "", cleaned)
    for pattern in FILLER_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = normalize_terms(cleaned)
    cleaned = normalize_spacing(cleaned)
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


def read_context(context_file: Path | None) -> str:
    if context_file is None or not context_file.exists():
        return ""
    content = context_file.read_text(encoding="utf-8", errors="replace").strip()
    content = re.sub(r"\s+", " ", content)
    return content[:800]
