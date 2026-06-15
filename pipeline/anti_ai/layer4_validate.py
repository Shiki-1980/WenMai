"""L4 实体锚定校验 —— 最后一层防线，逐段比对实体保全。

比 L3 输出与原段落逐句比对：
1. [[wikilink]] 引用完整性（已知实体一个不能少）
2. 裸实体名存在性（实体名在原文出现，改写后必须保留）
3. 任一校验失败 → 回退该段为原文
"""

from __future__ import annotations

import re

LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]")


def _extract_links(text: str) -> set[str]:
    return {m.strip() for m in LINK_RE.findall(text)}


def _extract_entity_occurrences(text: str, entity_names: set[str]) -> set[str]:
    """提取文本中出现的实体名（裸文本匹配）。"""
    found = set()
    for name in entity_names:
        if name in text:
            found.add(name)
    return found


def validate_layer4(
    original: str,
    polished: str,
    known_entities: set[str],
) -> tuple[str, list[str]]:
    """L4：实体锚定校验。

    逐段比对原文和改写后的实体保全情况。

    Args:
        original: 原始段落文本
        polished: 改写后的段落文本
        known_entities: 已知实体名集合

    Returns:
        (final_text, violations): final_text 为通过校验的文本或回退原文，
                                   violations 为违规描述列表。
    """
    violations: list[str] = []

    # 1. [[wikilink]] 检查
    orig_links = _extract_links(original)
    new_links = _extract_links(polished)

    missing_links = (orig_links & known_entities) - new_links
    if missing_links:
        violations.append(f"丢失 [[wikilink]] 实体: {sorted(missing_links)}")

    # 2. 裸实体名检查
    orig_entities = _extract_entity_occurrences(original, known_entities)
    new_entities = _extract_entity_occurrences(polished, known_entities)

    missing_bare = orig_entities - new_entities
    if missing_bare:
        violations.append(f"丢失裸实体名: {sorted(missing_bare)}")

    # 3. 额外实体检查（改写引入了不该有的新实体）
    extra = new_entities - orig_entities
    if extra:
        violations.append(f"引入了额外实体: {sorted(extra)}")

    if violations:
        return original, violations

    return polished, []


def validate_chapter(
    original_chapter: str,
    polished_chapter: str,
    known_entities: set[str],
) -> tuple[str, int]:
    """逐段校验整章：有违规的段回退原文。

    Returns:
        (final_chapter, violation_count): 最终文本和违规段数。
    """
    orig_paras = original_chapter.split("\n\n")
    new_paras = polished_chapter.split("\n\n")

    if len(orig_paras) != len(new_paras):
        return polished_chapter, 1

    total_violations = 0
    final_paras = []

    for orig, new in zip(orig_paras, new_paras):
        final, viols = validate_layer4(orig, new, known_entities)
        final_paras.append(final)
        if viols:
            total_violations += 1

    return "\n\n".join(final_paras), total_violations
