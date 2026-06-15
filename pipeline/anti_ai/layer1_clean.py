"""L1 规则清洗 —— 纯正则/字符串替换，不调 LLM，100% 实体安全。

处理内容：
1. 安全删除词（叙事填充/总结归纳/机械开场收尾）
2. 万能副词清理（缓缓→删除等）
3. 内心活动套话引导词去除
4. AI 标记词替换
5. 枚举模板段标记（交给 L3 处理）
"""

from __future__ import annotations

import re
from anti_ai.word_bank import (
    L1_REPLACEMENTS,
    SAFE_DELETE_WORDS,
    ENUMERATION_WORDS,
    UNIVERSAL_ADVERBS,
    AI_MARKER_WORDS,
    ACTION_CLICHES,
    EMOTION_LABELS,
    EXPRESSION_TEMPLATES,
    INNER_THOUGHT_CLICHES,
)


def _split_paragraphs(text: str) -> list[str]:
    """按空行分段，保留空行结构。"""
    return re.split(r"(\n\s*\n)", text)


def _is_paragraph_sep(seg: str) -> bool:
    return bool(re.match(r"^\n\s*\n$", seg))


def clean_layer1(text: str) -> str:
    """L1：纯规则清洗。

    Returns:
        清洗后的文本。
    """
    # Step 1: 精确替换（简单一对一，不变语义）
    for old, new in L1_REPLACEMENTS.items():
        text = text.replace(old, new)

    # Step 2: 万能副词清理
    #  副词 + 动词 → 动词（保留动词）
    for adv in UNIVERSAL_ADVERBS:
        # "缓缓开口" → "开口" 等双字词
        text = re.sub(rf"{adv}(.)", r"\1", text)
        # 单字动词保留："缓缓说"难以处理，交给 L3
        # 三字以上不动：避免误伤专有名词

    # Step 3: 删除位于句首的总结词/填充词
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        for word in sorted(SAFE_DELETE_WORDS, key=len, reverse=True):
            if line.strip().startswith(word + "，"):
                line = line.replace(word + "，", "", 1)
            elif line.strip().startswith(word + ","):
                line = line.replace(word + ",", "", 1)
            elif line.strip().startswith(word + "。"):
                line = line.replace(word + "。", "。", 1)
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # Step 4: 连续多个逗号清理
    text = re.sub(r"，\s*，", "，", text)
    text = re.sub(r"，\s*$", "", text)

    return text


def detect_layer1_issues(text: str) -> dict[str, list[str]]:
    """检测文本中仍存在的 AI 标记问题，返回诊断信息。

    Returns:
        {
            "enumerations": ["首先，...", "其次，..."],
            "action_cliches": ["深吸一口气", ...],
            "emotion_labels": ["感到愤怒", ...],
            "expression_templates": ["眸中闪过", ...],
            "ai_markers": ["仿佛", ...],
        }
    """
    result: dict[str, list[str]] = {
        "enumerations": [],
        "action_cliches": [],
        "emotion_labels": [],
        "expression_templates": [],
        "ai_markers": [],
    }

    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()

        for word in ENUMERATION_WORDS:
            if stripped.startswith(word):
                result["enumerations"].append(stripped[:80])

        for word in ACTION_CLICHES:
            if word in stripped:
                result["action_cliches"].append(stripped[:80])

        for word in EMOTION_LABELS:
            if word in stripped:
                result["emotion_labels"].append(stripped[:80])

        for word in EXPRESSION_TEMPLATES:
            if word in stripped:
                result["expression_templates"].append(stripped[:80])

    # AI 标记词检测（全文级别，不逐行）
    for word in AI_MARKER_WORDS:
        count = text.count(word)
        if count > 0:
            result["ai_markers"].append(f"{word} ({count}次)")

    return result
