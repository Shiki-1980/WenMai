"""JSON 状态 schema —— 实体事实的机器权威来源。

Entity facts are the single source of truth for character/item/location state.
Markdown frontmatter is retained as human-readable projection, but all programmatic
reads MUST go through state.json.

Design inspired by InkOS's CurrentStateFactSchema, adapted for WenMai's entity model.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# ── 允许的实体类型 ──────────────────────────────────────────────
ENTITY_TYPES = ("person", "item", "location", "concept")

# ── 按实体类型定义的允许谓词 ──────────────────────────────────────
ALLOWED_PREDICATES: dict[str, set[str]] = {
    "person": {
        "修为", "身份", "所在", "持有", "状态", "目标",
        "身体状态", "精神状态", "关系", "能力", "功法",
    },
    "item": {
        "current_holder", "location", "status", "category",
        "owner", "condition",
    },
    "location": {
        "parent_location", "掌控者/势力", "status", "population",
        "danger_level",
    },
    "concept": {
        "category", "status", "scope",
    },
}

# ── 允许的修为境界值（从世界观 concept 卡抽取，可扩展）────────────
CULTIVATION_REALMS = [
    # 凡胎三境
    "开脉境", "开脉境初期", "开脉境中期", "开脉境巅峰",
    "气海境", "气海境初期", "气海境中期", "气海境巅峰",
    "凝神境", "凝神境初期", "凝神境中期", "凝神境巅峰",
    # 超凡三境
    "真武境", "真武境初期", "真武境中期", "真武境巅峰",
    "天人境", "天人境初期", "天人境中期", "天人境巅峰",
    "意境级", "意境级初期", "意境级中期", "意境级巅峰",
    # 入圣三境
    "法相境", "法相境初期", "法相境中期", "法相境巅峰",
    "涅槃境", "涅槃境初期", "涅槃境中期", "涅槃境巅峰",
    "至尊境", "至尊境初期", "至尊境中期", "至尊境巅峰",
    # 特殊
    "凡人", "未知", "无法修炼",
    # 简写
    "开脉", "气海", "凝神", "真武", "天人", "意境", "法相", "涅槃", "至尊",
]

# ── 允许的状态值 ────────────────────────────────────────────────
ALLOWED_STATUSES = {
    "active", "injured", "incapacitated", "captured", "dead",
    "stub", "minor", "supporting", "major", "protagonist",
}


@dataclass
class EntityFact:
    """一条实体状态事实。"""
    predicate: str             # e.g. "修为", "所在", "持有"
    object: str                # e.g. "金丹四层", "青云宗后山"
    since_chapter: int         # 从哪章开始生效
    until_chapter: int | None = None  # 到哪章失效（None = 至今有效）
    source: str = ""           # 来源标识，e.g. "ch_022蒸馏"
    evidence: str = ""         # 从章节正文中引用的证据

    def to_dict(self) -> dict:
        d = asdict(self)
        # 去掉 None 值
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class EntityState:
    """单个实体的完整状态文件。"""
    entity: str
    entity_type: str           # person | item | location | concept
    last_updated_chapter: int = 0
    facts: list[EntityFact] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "entity_type": self.entity_type,
            "last_updated_chapter": self.last_updated_chapter,
            "facts": [f.to_dict() for f in self.facts],
        }

    def get_fact(self, predicate: str) -> EntityFact | None:
        """获取某谓词的最新有效事实。"""
        active = [
            f for f in self.facts
            if f.predicate == predicate and f.until_chapter is None
        ]
        return active[0] if active else None

    def get_all_active_facts(self) -> dict[str, str]:
        """返回所有当前有效的事实 {predicate: object}。"""
        return {
            f.predicate: f.object
            for f in self.facts
            if f.until_chapter is None
        }


@dataclass
class StateDelta:
    """单次蒸馏产出的状态变更 delta。"""
    entity: str
    entity_type: str
    chapter: int
    facts_added: list[EntityFact] = field(default_factory=list)
    facts_retired: list[tuple[str, str]] = field(default_factory=list)  # (predicate, object)


class StateValidator:
    """状态校验器 —— 在写入前检查数据合法性。"""

    def __init__(self, known_entity_names: set[str] | None = None):
        self.known_names = known_entity_names or set()

    def validate_fact(self, fact: EntityFact, entity_type: str) -> list[str]:
        """验证单条事实，返回错误列表（空列表表示通过）。"""
        errors = []

        # predicate 不能为空
        if not fact.predicate or not fact.predicate.strip():
            errors.append(f"事实谓词为空")
            return errors

        # object 不能为空
        if not fact.object or not fact.object.strip():
            errors.append(f"'{fact.predicate}' 的值不能为空")

        # since_chapter 必须是正整数
        if not isinstance(fact.since_chapter, int) or fact.since_chapter < 1:
            errors.append(f"since_chapter 必须是正整数，得到 {fact.since_chapter}")

        # until_chapter 如果存在，必须 > since_chapter
        if fact.until_chapter is not None:
            if not isinstance(fact.until_chapter, int) or fact.until_chapter <= fact.since_chapter:
                errors.append(
                    f"until_chapter ({fact.until_chapter}) 必须大于 since_chapter ({fact.since_chapter})"
                )

        # 谓词检查：对特定谓词做值域校验
        if fact.predicate == "修为":
            if fact.object not in CULTIVATION_REALMS:
                # 不阻断，但给出警告（新小说可能有自定义境界）
                pass

        if fact.predicate == "状态" or fact.predicate == "status":
            if fact.object not in ALLOWED_STATUSES and fact.object not in CULTIVATION_REALMS:
                pass  # 宽松通过，只警告

        # 实体类型对应的谓词白名单检查
        allowed = ALLOWED_PREDICATES.get(entity_type, set())
        if allowed and fact.predicate not in allowed:
            # 不硬阻断（LLM 可能产出白名单外的合理谓词），但记录
            pass

        return errors

    def validate_delta(self, delta: StateDelta, known_entities: set[str]) -> list[str]:
        """验证整个 delta，返回错误列表。"""
        errors = []

        if delta.entity not in known_entities:
            errors.append(f"实体 '{delta.entity}' 不在已知实体列表中")

        if delta.entity_type not in ENTITY_TYPES:
            errors.append(f"实体类型 '{delta.entity_type}' 不合法，允许: {ENTITY_TYPES}")

        for fact in delta.facts_added:
            errors.extend(self.validate_fact(fact, delta.entity_type))

        return errors


def load_entity_state(state_path: Path) -> EntityState | None:
    """从 .state.json 文件加载实体状态。"""
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text("utf-8"))
        facts = [
            EntityFact(
                predicate=f["predicate"],
                object=f["object"],
                since_chapter=f.get("since_chapter", 0),
                until_chapter=f.get("until_chapter"),
                source=f.get("source", ""),
                evidence=f.get("evidence", ""),
            )
            for f in data.get("facts", [])
        ]
        return EntityState(
            entity=data["entity"],
            entity_type=data.get("entity_type", "person"),
            last_updated_chapter=data.get("last_updated_chapter", 0),
            facts=facts,
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"  [WARN] 加载状态文件失败 {state_path}: {e}")
        return None


def save_entity_state(state: EntityState, state_path: Path):
    """保存实体状态到 .state.json 文件。"""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        "utf-8",
    )


def apply_delta_to_state(state: EntityState, delta: StateDelta) -> EntityState:
    """Immutable apply: 将 delta 应用到 EntityState，返回新状态。"""
    new_facts = copy.deepcopy(state.facts)

    # 退休被覆盖的旧事实
    for fact in delta.facts_added:
        # 找到同谓词的活跃事实，标记为退休
        for old_fact in new_facts:
            if old_fact.predicate == fact.predicate and old_fact.until_chapter is None:
                old_fact.until_chapter = delta.chapter

    # 处理显式退休
    for predicate, _object in delta.facts_retired:
        for old_fact in new_facts:
            if old_fact.predicate == predicate and old_fact.until_chapter is None:
                old_fact.until_chapter = delta.chapter

    # 添加新事实
    new_facts.extend(delta.facts_added)

    return EntityState(
        entity=state.entity,
        entity_type=state.entity_type,
        last_updated_chapter=delta.chapter,
        facts=new_facts,
    )


def state_to_markdown_fragment(state: EntityState) -> str:
    """从 state.json 生成 markdown 状态摘要（供 prompt 用）。"""
    active = state.get_all_active_facts()
    if not active:
        return f"（无结构化状态记录）"

    lines = []
    priority_order = ["修为", "身份", "所在", "持有", "状态", "目标", "身体状态"]

    for pred in priority_order:
        if pred in active:
            lines.append(f"- {pred}：{active[pred]}")

    # 其余谓词
    for pred, val in active.items():
        if pred not in priority_order:
            lines.append(f"- {pred}：{val}")

    return "\n".join(lines)
