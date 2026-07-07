"""Knowledge-base extractor for the Bubble Buddy support skills.

Reads the application source (``src/copilot_voice_shell``) and emits *derived*
data files — a config schema and a user-facing message catalog — that the
skills ship instead of the source itself. Run at dev time / in CI on release;
the generated JSON is what lands in the skill's ``references/`` folder.

  python tools/gen-kb/gen_kb.py            # writes to skills/bubble-buddy/references

Design note: NO source code is copied into the output. Only distilled facts
(key names, defaults, enums, message templates) are extracted. The skills point
at source by *path/symbol* for on-demand retrieval, never by embedding it.
"""
from __future__ import annotations

import ast
import io
import json
import re
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "copilot_voice_shell"
SKILLS = REPO_ROOT / "skills"

_ENUM_RE = re.compile(r"([\w\-./]+(?:\s*\|\s*[\w\-./]+)+)")


def _line_comments(source: str) -> dict[int, str]:
    """Map 1-based line number -> trailing comment text (without the '#')."""
    comments: dict[int, str] = {}
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            comments[tok.start[0]] = tok.string.lstrip("#").strip()
    return comments


def _enum_from_comment(comment: str) -> list[str] | None:
    """Extract an enum list from a 'a | b | c' style comment, if present.

    Parentheticals (e.g. "aad (use az login) | api_key") are stripped first so
    an inline explanation doesn't hide the enum from the pipe matcher."""
    if not comment:
        return None
    cleaned = re.sub(r"\([^)]*\)", " ", comment)
    m = _ENUM_RE.search(cleaned)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split("|")]
    return parts if len(parts) > 1 else None


def _json_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _describe(key: str, value: object, comment: str) -> dict:
    entry: dict = {"default": value, "type": _json_type(value)}
    enum = _enum_from_comment(comment)
    if enum:
        entry["enum"] = enum
    if comment:
        entry["note"] = comment
    entry["secret"] = key.split(".")[-1] in {"api_key"}
    return entry


def extract_config_schema() -> dict:
    """Parse ``config.py`` DEFAULTS into a flat key->metadata schema.

    Nested ``azure.*`` keys are flattened with dotted names. Trailing inline
    comments are used to recover enums and descriptions."""
    source = (SRC / "config.py").read_text(encoding="utf-8")
    comments = _line_comments(source)
    tree = ast.parse(source)

    defaults_node = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "DEFAULTS":
                    defaults_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "DEFAULTS":
                defaults_node = node.value
    if not isinstance(defaults_node, ast.Dict):
        raise SystemExit("Could not locate DEFAULTS dict in config.py")

    keys: dict[str, dict] = {}

    def walk(dict_node: ast.Dict, prefix: str = "") -> None:
        for k_node, v_node in zip(dict_node.keys, dict_node.values):
            if not isinstance(k_node, ast.Constant):
                continue
            key = f"{prefix}{k_node.value}"
            comment = comments.get(getattr(v_node, "lineno", -1), "")
            if isinstance(v_node, ast.Dict):
                if v_node.keys:
                    walk(v_node, prefix=f"{key}.")
                else:
                    # Empty dict: a real leaf key (e.g. polish_prompts: {}), not
                    # a namespace — emit it instead of silently dropping it.
                    keys[key] = _describe(key, {}, comment)
                continue
            try:
                value = ast.literal_eval(v_node)
            except Exception:  # noqa: BLE001
                value = None
            keys[key] = _describe(key, value, comment)

    walk(defaults_node)
    return {
        "app": "Bubble Buddy",
        "generated_from": "src/copilot_voice_shell/config.py:DEFAULTS",
        "config_path": "%USERPROFILE%/.copilot-voice-shell/config.json (Windows), ~/.copilot-voice-shell/config.json (macOS/Linux)",
        "keys": keys,
    }


def extract_messages() -> dict:
    """Parse the i18n catalog for user-facing message/bubble templates so the
    doctor skill can recognise text a user quotes from the app UI."""
    source = (SRC / "i18n.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    strings_node = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in {"STRINGS", "_STRINGS", "CATALOG"}:
                    strings_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in {"STRINGS", "_STRINGS", "CATALOG"}:
                strings_node = node.value
    if strings_node is None:
        # Fallback: first module-level dict assignment whose values are lang dicts.
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Dict):
                strings_node = node.value
                break
    if not isinstance(strings_node, ast.Dict):
        raise SystemExit("Could not locate i18n catalog dict in i18n.py")

    messages: dict[str, dict] = {}
    for k_node, v_node in zip(strings_node.keys, strings_node.values):
        if not isinstance(k_node, ast.Constant):
            continue
        key = str(k_node.value)
        if not (key.startswith("msg.") or key.startswith("bubble.")):
            continue
        try:
            value = ast.literal_eval(v_node)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(value, dict):
            messages[key] = {lang: value[lang] for lang in ("zh", "en") if lang in value}
    return {
        "app": "Bubble Buddy",
        "generated_from": "src/copilot_voice_shell/i18n.py catalog (msg.* / bubble.*)",
        "messages": messages,
    }


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(REPO_ROOT)}  ({len(json.dumps(data))} bytes)")


def main() -> None:
    schema = extract_config_schema()
    messages = extract_messages()

    _write(SKILLS / "bubble-buddy" / "references" / "config.schema.json", schema)
    _write(SKILLS / "bubble-buddy" / "references" / "messages.json", messages)
    print("KB generation complete.")


if __name__ == "__main__":
    main()
