"""实体消歧 —— 蒸馏输出消歧 + 检索结果消歧 bias。

两个消歧场景：

1. 蒸馏输出消歧（DisambiguationLinker）
   蒸馏发现"新"实体名 → 判断是新角色还是旧角色的别名
   用 LLM 做相似度判断，三级置信度处理

2. 检索结果消歧（RetrievalDisambiguator）
   倒排查返回多个候选 → 用空间/时间/弧线 bias 排序
   纯代码逻辑，不调 LLM
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── 消歧结果 ──────────────────────────────────────────────────

class DisambigAction:
    AUTO = "auto"        # 自动合并/确认
    WARN = "warn"        # 接受但标记 warning
    PENDING = "pending"  # 标记待人工确认


@dataclass
class DisambigResult:
    """一次消歧的结果。"""
    candidate: str                     # 候选名（原文中的名字）
    resolved_to: str | None = None    # 解析为的 canonical 实体名（None = 新实体）
    confidence: float = 0.0            # 0-1
    action: str = DisambigAction.AUTO
    reason: str = ""


@dataclass
class PendingEntity:
    """等待确认的实体。"""
    candidate: str
    first_seen_chapter: int
    chapters_seen: int = 1
    entity_type: str = "person"


# ── 蒸馏输出消歧（用 LLM）─────────────────────────────────────

DISAMBIG_SYSTEM = """你是小说实体消歧助手。你的任务是判断一个新出现的名字是否是已知实体的别名。

判断标准：
1. 名字完全相同或仅繁简差异 → 同一实体（confidence ≥ 0.95）
2. 名字部分匹配（如"青衫少年" vs "陆沉"的特征描述）→ 可能是别名
3. 名字完全不同但上下文暗示同一人 → 可能是别名
4. 名字完全不同且上下文无关联 → 新实体

只输出 JSON，不要任何其他内容。"""

DISAMBIG_USER = """判断以下"候选名"是否是已知实体的别名：

候选名: {candidate}
所在章节上下文（摘录）: {context}

已知实体列表:
{known_list}

输出 JSON:
{{
  "resolved_to": "已知实体名" 或 null（如果是新实体）,
  "confidence": 0.0-1.0,
  "reason": "一句话理由"
}}"""


class DisambiguationLinker:
    """蒸馏输出消歧器 —— 判断新名字是否是已知实体的别名。"""

    def __init__(self, generator=None):
        self.generator = generator
        self._pending: dict[str, PendingEntity] = {}

    def disambiguate(
        self,
        candidate_name: str,
        known_entities: list[str],
        context: str = "",
    ) -> DisambigResult:
        """消歧一个候选名。"""
        # 1. 精确匹配（包括已知别名）
        if candidate_name in known_entities:
            return DisambigResult(
                candidate=candidate_name,
                resolved_to=candidate_name,
                confidence=1.0,
                action=DisambigAction.AUTO,
                reason="精确匹配已知实体名",
            )

        if not self.generator:
            # 无 LLM：假定为新实体
            return DisambigResult(
                candidate=candidate_name,
                action=DisambigAction.PENDING if len(candidate_name) >= 3 else DisambigAction.AUTO,
                reason="无 LLM，无法做语义消歧",
            )

        # 2. LLM 语义消歧
        known_list = "\n".join(f"- {n}" for n in known_entities[:50])
        prompt = DISAMBIG_USER.format(
            candidate=candidate_name,
            context=context[:500],
            known_list=known_list,
        )

        try:
            raw = self.generator.generate(DISAMBIG_SYSTEM, prompt, json_mode=True)
            data = self.generator._parse_json(raw)
        except Exception:
            data = {}

        resolved = data.get("resolved_to") if data else None
        confidence = float(data.get("confidence", 0.0)) if data else 0.0
        reason = data.get("reason", "") if data else ""

        # 3. 三级判定
        if resolved and confidence >= 0.8:
            return DisambigResult(
                candidate=candidate_name,
                resolved_to=resolved,
                confidence=confidence,
                action=DisambigAction.AUTO,
                reason=reason,
            )
        elif resolved and confidence >= 0.5:
            return DisambigResult(
                candidate=candidate_name,
                resolved_to=resolved,
                confidence=confidence,
                action=DisambigAction.WARN,
                reason=reason,
            )
        else:
            return DisambigResult(
                candidate=candidate_name,
                resolved_to=None,
                confidence=confidence,
                action=DisambigAction.PENDING,
                reason=reason if reason else "可能是新实体",
            )

    def track_pending(self, result: DisambigResult, chapter: int):
        """记录 pending 消歧项。"""
        if result.action == DisambigAction.PENDING:
            if result.candidate not in self._pending:
                self._pending[result.candidate] = PendingEntity(
                    candidate=result.candidate,
                    first_seen_chapter=chapter,
                )
            else:
                self._pending[result.candidate].chapters_seen += 1

    def auto_confirm_stale(self, chapter: int, max_pending_chapters: int = 3) -> list[str]:
        """自动确认超过 N 章仍未确认的 pending 实体为正式新实体。"""
        confirmed = []
        for name, pe in list(self._pending.items()):
            if chapter - pe.first_seen_chapter >= max_pending_chapters:
                confirmed.append(name)
                del self._pending[name]
        return confirmed

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def get_pending(self) -> dict[str, PendingEntity]:
        return dict(self._pending)


# ── 检索结果消歧（纯代码，不调 LLM）───────────────────────────

@dataclass
class ChapterContext:
    """消歧时需要的当前章节上下文。"""
    current_chapter: int
    protagonist_location: str = ""      # 主角当前所在
    arc_entities: list[str] = field(default_factory=list)  # 本 arc 的 key_entities
    entity_first_chapter: dict[str, int] = field(default_factory=dict)  # 实体首现章节
    entity_last_chapter: dict[str, int] = field(default_factory=dict)   # 实体末现章节
    entity_links: dict[str, list[str]] = field(default_factory=dict)    # 实体的 wikilink 链接


class RetrievalDisambiguator:
    """检索结果消歧器 —— 当倒排查返回多个候选时，用已有数据排序。

    不需要 LLM，只用空间/时间/弧线 bias。
    """

    # 权重配置
    SPATIAL_BIAS = 100       # 主角所在位置匹配
    TEMPORAL_ELIMINATE = -1000  # 未来实体直接淘汰
    TEMPORAL_STALE = -50     # 超过 20 章未出现的实体降权
    ARC_BIAS = 50            # arc key_entities 中的实体加分
    MUTUAL_LINK_BIAS = 30    # 候选间互相链接加分

    def resolve(
        self,
        candidates: list[str],
        context: ChapterContext,
    ) -> list[str]:
        """从候选列表中选出最可能的实体。返回排序后的列表。"""
        if len(candidates) <= 1:
            return list(candidates)

        scored: list[tuple[str, float]] = []
        for name in candidates:
            score = self._score(name, context)
            if score > -900:  # 没被淘汰
                scored.append((name, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored]

    def _score(self, entity_name: str, ctx: ChapterContext) -> float:
        """为一个候选实体计算消歧分数。"""
        score = 0.0

        # 时间 bias：未出现的实体直接淘汰
        first = ctx.entity_first_chapter.get(entity_name, 0)
        if first > ctx.current_chapter:
            return self.TEMPORAL_ELIMINATE

        # 时间 bias：最近出现过的加分
        last = ctx.entity_last_chapter.get(entity_name, 0)
        if last > 0 and ctx.current_chapter - last <= 5:
            score += 20
        elif ctx.current_chapter - last > 20:
            score += self.TEMPORAL_STALE

        # 弧线 bias
        if entity_name in ctx.arc_entities:
            score += self.ARC_BIAS

        # 空间 bias：需要知道实体的"所在"——从 entity_links 间接判断
        # 如果候选 A 链接到主角所在位置 → 加分
        links = ctx.entity_links.get(entity_name, [])
        if ctx.protagonist_location:
            if ctx.protagonist_location in links:
                score += self.SPATIAL_BIAS
            # 如果候选就是主角所在位置本身
            if entity_name == ctx.protagonist_location:
                score += self.SPATIAL_BIAS

        # 互锁 bias：候选间互相链接
        # 这需要在 resolve 中计算，这里只做基础分

        return score

    def resolve_with_context(
        self,
        term: str,
        candidates: list[str],
        context: ChapterContext,
        all_recalled: set[str],
    ) -> list[str]:
        """带互锁信息的消歧。

        all_recalled: 本轮检索所有被召回的实体集合，
        用于计算互锁 bias。
        """
        scored = []
        for name in candidates:
            score = self._score(name, context)

            # 互锁 bias：如果候选 A 链接到候选 B，且 B 也被召回了
            links = set(context.entity_links.get(name, []))
            mutual = links & all_recalled
            if mutual:
                score += self.MUTUAL_LINK_BIAS * len(mutual)

            if score > -900:
                scored.append((name, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored]
