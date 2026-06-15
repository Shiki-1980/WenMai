"""L3 创意润色 —— 仅对高风险段落调用 LLM，每次传最小上下文（单段或单段对话）。

安全策略：
- 每段独立处理，不传整章上下文
- 少即多：段落越短，LLM 越难跑偏
- 只对命中 AI 标记的段落调用 LLM
"""

from __future__ import annotations

from anti_ai.word_bank import (
    EMOTION_LABELS,
    ACTION_CLICHES,
    EXPRESSION_TEMPLATES,
    ENUMERATION_WORDS,
    LOGIC_CONNECTORS,
)

L3_SYSTEM_PROMPT = """你是资深网文编辑。将下面这段文字改写为更自然的网文风格。

铁律（不可违反）：
1. 不改任何专有名词：人名、地名、功法名、物品名全保留原样不动
2. 不改剧情、事件顺序、对话含义
3. 不改 [[wikilink]] 双链

改写要求：
- 抽象情绪标签 → 用生理反应+微动作替换（不要写"他感到X"）
- 套话动作 → 用具体行为替换
- 神态模板 → 按角色性格个性化
- 段落长短有一定变化
- 对话前缀去掉多余修饰，优先用前置动作

直接输出改写后的段落。不要任何前言、解释或标注。"""


def _needs_layer3(paragraph: str) -> bool:
    """判断段落是否需要 L3 LLM 处理。"""
    stripped = paragraph.strip()
    if len(stripped) < 15:
        return False

    # 命中情绪标签
    for word in EMOTION_LABELS:
        if word in stripped:
            return True

    # 命中动作套话
    for word in ACTION_CLICHES:
        if word in stripped:
            return True

    # 命中神态模板
    for word in EXPRESSION_TEMPLATES:
        if word in stripped:
            return True

    return False


def polish_layer3(gen, text: str, max_paragraphs: int = 8) -> str:
    """L3：对命中 AI 标记的段落，逐段调 LLM 改写。

    Args:
        gen: LLMGenerator 实例
        text: 整章文本
        max_paragraphs: 最多处理 N 段（控制成本）

    Returns:
        改写后的完整文本。
    """
    paragraphs = text.split("\n\n")
    l3_count = 0

    result = []
    for para in paragraphs:
        stripped = para.strip()

        if not stripped:
            result.append(para)
            continue

        if _needs_layer3(stripped) and l3_count < max_paragraphs:
            polished = _polish_one_paragraph(gen, stripped)
            if polished and polished != stripped:
                result.append(polished)
                l3_count += 1
                continue

        result.append(para)

    return "\n\n".join(result)


def _polish_one_paragraph(gen, paragraph: str) -> str:
    """对单段调用 LLM 改写，限制 800 字。"""
    try:
        truncated = paragraph[:800]
        return gen.generate(L3_SYSTEM_PROMPT, truncated, temperature=0.6) or paragraph
    except Exception:
        return paragraph
