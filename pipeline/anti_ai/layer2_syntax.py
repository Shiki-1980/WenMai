"""L2 句法调优 —— 纯规则驱动的句式变化，不调 LLM，100% 实体安全。

处理内容：
1. 段落长度差异化（等长段落 → 长短交替）
2. "了"字密度降低（句尾"了" → 删除或替换）
3. 连续同构句打断（同主语连续句 → 插入短句）
4. 长句逗号过多拆分
"""

from __future__ import annotations

import re


def tune_layer2(text: str) -> str:
    """L2：句法调优，全部规则驱动。

    Returns:
        调优后的文本。
    """
    text = _reduce_le_density(text)
    text = _break_long_sentences(text)
    text = _vary_paragraph_length(text)
    text = _add_variation_anchors(text)
    return text


def _reduce_le_density(text: str) -> str:
    """降低"了"字密度。

    规则：
    - 句尾"了。" → 保留（完成体标记）
    - "做了/看了/说了/写了"等轻动词 + "了" → 尝试删除"了"
    - 连续两句含"了" → 第二句去掉（避免 AI 味）
    """
    lines = text.split("\n")
    result = []
    prev_has_le = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append(line)
            prev_has_le = False
            continue

        count_le = stripped.count("了")
        if count_le == 0:
            prev_has_le = False
            result.append(line)
            continue

        # 轻动词 + 了 → 去"了"（走/看/说/拿/写/做/吃/喝 + 了 + 补语）
        # 例如: "走了过去" → "走过去"
        # 例如: "看了看" → "看看"
        modified = re.sub(r"(走|看|说|拿|写|做|吃|喝|跑|站|坐)了(过|一|起|下|上|进|出|回|开|到|见|着)", r"\1\2", line)

        # AA了 → AA（看了看 → 看看）
        modified = re.sub(r"(.)了\1", r"\1\1", modified)

        # 连续两行都有"了"，第二行尝试去"了"
        if prev_has_le and count_le <= 2:
            modified = modified.replace("了。", "。")
            modified = modified.replace("了，", "，")

        if modified != line:
            result.append(modified)
            prev_has_le = modified.count("了") > 0
        else:
            result.append(line)
            prev_has_le = count_le > 0

    return "\n".join(result)


def _break_long_sentences(text: str) -> str:
    """拆分逗号过多的长句（>= 5 个逗号）。"""
    lines = text.split("\n")
    result = []

    for line in lines:
        stripped = line.strip()
        comma_count = stripped.count("，")

        if comma_count < 5:
            result.append(line)
            continue

        # 在第3或第4个逗号处拆句
        parts = stripped.split("，")
        if len(parts) >= 6:
            mid = min(3, len(parts) // 2)
            first = "，".join(parts[:mid]) + "。"
            second = "，".join(parts[mid:]).lstrip("，")
            result.append(first if not first.startswith("。") else first[1:])
            result.append(second)
        else:
            result.append(line)

    return "\n".join(result)


def _vary_paragraph_length(text: str) -> str:
    """段落长度差异化：如果连续 3 段长度相近（波动<30%），在第 3 段中插入短句打断。"""
    paragraphs = re.split(r"(\n\s*\n)", text)
    seg_info = []

    for i, seg in enumerate(paragraphs):
        if _is_paragraph_sep(seg):
            seg_info.append((i, None))
        else:
            seg_info.append((i, len(seg.strip())))

    # 找连续 3 段等长的模式
    i = 0
    while i < len(seg_info) - 2:
        idx1, len1 = seg_info[i]
        idx2, len2 = seg_info[i + 1]
        idx3, len3 = seg_info[i + 2]

        if None in (len1, len2, len3):
            i += 1
            continue

        if len1 == 0 or len2 == 0 or len3 == 0:
            i += 1
            continue

        avg = (len1 + len2 + len3) / 3
        if avg < 30:  # 短段落不用处理
            i += 1
            continue

        # 波动 < 30% 视为等长
        if abs(len1 - avg) / avg > 0.3:
            i += 1
            continue

        # 在第 3 段末尾添加短句或拆分
        seg = paragraphs[idx3].rstrip()
        sentences = re.split(r"(?<=[。！？])\s*", seg)
        if len(sentences) >= 2:
            # 最后一个句子独立成段
            paragraphs[idx3] = "。".join(sentences[:-1]) + "。"
            paragraphs.insert(idx3 + 1, "\n\n" + sentences[-1].strip())

        i += 3

    return "".join(paragraphs)


def _add_variation_anchors(text: str) -> str:
    """在信息密集段中插入动作锚点：连续 3+ 句无对话/动作的说明句 → 给一段加 [ACTION] 标记。

    注意：这只是标记，实际改写由 L3 LLM 完成。
    """
    # 暂不做标记插入，L3 会按段落检查
    return text


def _is_paragraph_sep(seg: str) -> bool:
    return bool(re.match(r"^\n\s*\n$", seg))
