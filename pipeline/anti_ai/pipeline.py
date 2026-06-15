"""去AI味分层处理编排器。

执行顺序：
1. L1 规则清洗（纯规则，零风险）
2. L2 句法调优（纯规则，零风险）
3. L3 创意润色（LLM 逐段，低风险）
4. L4 实体锚定校验（兜底，有违规即回退）

L1+L2 覆盖约 70% 的去AI味工作且完全安全。
L3 仅处理命中标记的段落（通常 < 10% 的段落）。
L4 确保实体不丢失，违规段回退原文。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from generator import LLMGenerator

from anti_ai.layer1_clean import clean_layer1, detect_layer1_issues
from anti_ai.layer2_syntax import tune_layer2
from anti_ai.layer3_llm import polish_layer3
from anti_ai.layer4_validate import validate_layer4, validate_chapter

logger = logging.getLogger(__name__)


class AntiAIReport:
    """去AI味处理报告。"""

    def __init__(self):
        self.l1_issues: dict = {}
        self.l2_applied: bool = False
        self.l3_paragraphs: int = 0
        self.l4_violations: int = 0
        self.l4_fallbacks: int = 0
        self.original_len: int = 0
        self.final_len: int = 0

    @property
    def pass_all(self) -> bool:
        return self.l4_violations == 0

    def summary(self) -> str:
        lines = [
            f"L1 检测: {sum(len(v) for v in self.l1_issues.values())} 处 AI 标记",
            f"L2 句法调优: {'已完成' if self.l2_applied else '未执行'}",
            f"L3 LLM 润色: {self.l3_paragraphs} 段",
            f"L4 校验: {self.l4_violations} 段违规 (已回退)",
            f"字数: {self.original_len} → {self.final_len}",
        ]
        return "\n".join(lines)


def run_anti_ai_pipeline(
    gen: Optional[LLMGenerator],
    chapter_text: str,
    known_entities: set[str] | None = None,
    enable_l3: bool = True,
    max_l3_paragraphs: int = 8,
) -> tuple[str, AntiAIReport]:
    """执行完整去AI味流水线。

    Args:
        gen: LLMGenerator 实例（L3 需要，不传则跳过 L3）
        known_entities: 已知实体名集合（L4 需要，不传则跳过 L4）
        enable_l3: 是否启用 L3 LLM 润色
        max_l3_paragraphs: L3 最多处理段数

    Returns:
        (final_text, report): 处理后的文本和详细报告。
    """
    report = AntiAIReport()
    report.original_len = len(chapter_text)

    text = chapter_text

    # ═══ L1: 规则清洗（100% 安全） ═══
    report.l1_issues = detect_layer1_issues(text)
    text = clean_layer1(text)

    # ═══ L2: 句法调优（100% 安全） ═══
    text = tune_layer2(text)
    report.l2_applied = True

    # ═══ L3: LLM 创意润色（低风险，逐段处理） ═══
    if enable_l3 and gen is not None:
        before_l3 = text
        text = polish_layer3(gen, text, max_paragraphs=max_l3_paragraphs)
        # 计数实际被处理的段落数
        orig_paras = before_l3.split("\n\n")
        new_paras = text.split("\n\n")
        report.l3_paragraphs = sum(
            1 for o, n in zip(orig_paras, new_paras) if o != n
        )

    # ═══ L4: 实体锚定校验（兜底） ═══
    if known_entities:
        text, report.l4_violations = validate_chapter(
            chapter_text, text, known_entities
        )

    report.final_len = len(text)
    return text, report
