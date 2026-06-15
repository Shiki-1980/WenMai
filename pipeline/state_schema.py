"""JSON 状态 schema —— 实体事实的机器权威来源。

Entity facts are the single source of truth for character/item/location state.
Markdown frontmatter is retained as human-readable projection, but all programmatic
reads MUST go through state.json.

The schema is now per-novel: each novel has its own novel_schema.json that defines
entity types, predicates, allowed values, override policies, and markdown templates.
No more hardcoded cultivation realms or predicate lists.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    """验证问题的严重程度。"""
    WARN = "warn"      # 记录但不阻断（enum 值不在允许列表、未知实体等）
    ERROR = "error"    # 可重试的问题（数据不一致）
    FATAL = "fatal"    # 放弃本次更新（since_chapter 非法、谓词为空等）


# ── 常量 ──────────────────────────────────────────────────────
ENTITY_TYPES = ("person", "item", "location", "concept")
SCHEMA_FILENAME = "novel_schema.json"


def _atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".tmp", prefix=path.name + ".", dir=path.parent)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


# ── Override Policy ────────────────────────────────────────────

class OverridePolicy(Enum):
    """字段的覆盖策略。"""
    LOCKED = "locked"                  # 一旦设定，任何章节不可覆盖
    APPEND_ONLY = "append_only"        # 只能新增，不能删除或修改已有值
    OVERRIDE_ALLOWED = "override_allowed"  # 章节级可覆盖（默认）


# ── Schema 数据类 ──────────────────────────────────────────────

@dataclass
class PredicateDef:
    """一个谓词的定义（来自 novel_schema.json）。"""
    name: str                        # 谓词名 e.g. "修为"
    type: str                        # "enum" | "string" | "list"
    category: str = ""               # 分组 e.g. "实力", "基础", "能力"
    priority: int = 99               # 显示排序，越小越靠前
    override: OverridePolicy = OverridePolicy.OVERRIDE_ALLOWED
    values: list[str] = field(default_factory=list)   # enum 类型的允许值
    description: str = ""            # 给 LLM 看的说明

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "category": self.category,
            "priority": self.priority,
            "override": self.override.value,
            "values": self.values,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict) -> PredicateDef:
        override_raw = data.get("override", "override_allowed")
        if isinstance(override_raw, str):
            override = OverridePolicy(override_raw)
        else:
            override = OverridePolicy.OVERRIDE_ALLOWED
        return cls(
            name=name,
            type=data.get("type", "string"),
            category=data.get("category", ""),
            priority=data.get("priority", 99),
            override=override,
            values=data.get("values", []),
            description=data.get("description", ""),
        )


@dataclass
class EntitySchema:
    """一种实体类型的 schema 定义。"""
    entity_type: str                 # "person" | "item" | "location" | "concept"
    label: str = ""                  # 中文标签 e.g. "人物"
    predicates: dict[str, PredicateDef] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    markdown_template: str = ""      # JSON → Markdown 渲染模板

    def get_predicate(self, name: str) -> PredicateDef | None:
        return self.predicates.get(name)

    def predicates_sorted(self) -> list[PredicateDef]:
        """按 priority 排序的谓词列表。"""
        return sorted(self.predicates.values(), key=lambda p: p.priority)

    def predicates_by_category(self) -> dict[str, list[PredicateDef]]:
        """按 category 分组的谓词。"""
        groups: dict[str, list[PredicateDef]] = {}
        for p in self.predicates.values():
            cat = p.category or "其他"
            if cat not in groups:
                groups[cat] = []
            groups[cat].append(p)
        return groups

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "predicates": {name: p.to_dict() for name, p in self.predicates.items()},
            "tags": self.tags,
            "markdown_template": self.markdown_template,
        }

    @classmethod
    def from_dict(cls, entity_type: str, data: dict) -> EntitySchema:
        predicates = {}
        for name, pdata in data.get("predicates", {}).items():
            predicates[name] = PredicateDef.from_dict(name, pdata)
        return cls(
            entity_type=entity_type,
            label=data.get("label", ""),
            predicates=predicates,
            tags=data.get("tags", []),
            markdown_template=data.get("markdown_template", ""),
        )


class NovelSchema:
    """从 novel_schema.json 加载的整本小说 schema。

    使用方式:
        schema = NovelSchema.load(Path("novels/万古劫烬"))
        person_preds = schema.get_predicates("person")
        schema.validate_fact(fact, "person")
    """

    def __init__(self):
        self.novel: str = ""
        self.schema_version: int = 1
        self.generated_at: str = ""
        self.generated_by: str = ""
        self.entity_schemas: dict[str, EntitySchema] = {}
        self.locked_fields: list[str] = field(default_factory=list)
        self.append_only_fields: list[str] = field(default_factory=list)
        self.ignore_patterns: list[str] = field(default_factory=list)
        self.min_recall: int = 3
        self.max_hop: int = 1
        self.max_entity_cards: int = 30

    # ── 查询接口 ──

    def get_entity_schema(self, entity_type: str) -> EntitySchema | None:
        return self.entity_schemas.get(entity_type)

    def get_predicates(self, entity_type: str) -> dict[str, PredicateDef]:
        es = self.get_entity_schema(entity_type)
        return es.predicates if es else {}

    def get_predicate_def(self, entity_type: str, predicate: str) -> PredicateDef | None:
        es = self.get_entity_schema(entity_type)
        return es.predicates.get(predicate) if es else None

    def get_allowed_values(self, entity_type: str, predicate: str) -> list[str]:
        """返回 enum 类型谓词的允许值列表；非 enum 返回空列表。"""
        pdef = self.get_predicate_def(entity_type, predicate)
        return pdef.values if pdef and pdef.type == "enum" else []

    def get_override_policy(self, entity_type: str, predicate: str) -> OverridePolicy:
        """返回某谓词的覆盖策略。"""
        pdef = self.get_predicate_def(entity_type, predicate)
        return pdef.override if pdef else OverridePolicy.OVERRIDE_ALLOWED

    def get_all_entity_types(self) -> list[str]:
        return list(self.entity_schemas.keys())

    # ── 校验 ──

    def validate_fact(self, fact: EntityFact, entity_type: str) -> list[tuple[str, ValidationSeverity]]:
        """校验单条事实，返回 (错误描述, 严重程度) 列表（空 = 通过）。"""
        errors: list[tuple[str, ValidationSeverity]] = []

        if not fact.predicate or not fact.predicate.strip():
            errors.append(("事实谓词为空", ValidationSeverity.FATAL))
            return errors
        if not fact.object or not fact.object.strip():
            errors.append((f"'{fact.predicate}' 的值不能为空", ValidationSeverity.FATAL))
        if not isinstance(fact.since_chapter, int) or fact.since_chapter < 1:
            errors.append(
                (f"since_chapter 必须是正整数，得到 {fact.since_chapter}", ValidationSeverity.FATAL)
            )
        if fact.until_chapter is not None:
            if not isinstance(fact.until_chapter, int) or fact.until_chapter <= fact.since_chapter:
                errors.append(
                    (f"until_chapter ({fact.until_chapter}) 必须 > since_chapter ({fact.since_chapter})",
                     ValidationSeverity.ERROR)
                )

        # 检查谓词是否在 schema 中定义
        pdef = self.get_predicate_def(entity_type, fact.predicate)
        if pdef is None:
            # 未定义的谓词：记录为 WARN（LLM 可能产出 schema 之外的新属性）
            errors.append(
                (f"谓词 '{fact.predicate}' 未在 schema 中定义（{entity_type}）",
                 ValidationSeverity.WARN)
            )
        else:
            # enum 类型检查值是否在允许列表中 → WARN（schema 可能不完整）
            if pdef.type == "enum" and pdef.values:
                if fact.object not in pdef.values:
                    errors.append(
                        (f"'{fact.predicate}' 的值 '{fact.object}' 不在允许列表中: {pdef.values}",
                         ValidationSeverity.WARN)
                    )

        return errors

    def validate_delta(self, delta: StateDelta, known_entities: set[str]) -> list[tuple[str, ValidationSeverity]]:
        """校验整个 delta。"""
        errors: list[tuple[str, ValidationSeverity]] = []
        if delta.entity not in known_entities:
            errors.append(
                (f"实体 '{delta.entity}' 不在已知实体列表中", ValidationSeverity.WARN)
            )
        if delta.entity_type not in ENTITY_TYPES:
            errors.append(
                (f"实体类型 '{delta.entity_type}' 不合法，允许: {ENTITY_TYPES}", ValidationSeverity.ERROR)
            )
        for fact in delta.facts_added:
            errors.extend(self.validate_fact(fact, delta.entity_type))
        return errors

    def check_override_violation(
        self, fact: EntityFact, existing_state: EntityState | None
    ) -> str | None:
        """检查一条新事实是否违反 override policy。
        返回错误描述，或 None（无违反）。
        """
        policy = self.get_override_policy(fact.entity_type if hasattr(fact, 'entity_type') else "person", fact.predicate)

        if policy == OverridePolicy.LOCKED and existing_state:
            old = existing_state.get_fact(fact.predicate)
            if old and old.object != fact.object:
                return (
                    f"LOCKED 字段 '{fact.predicate}' 尝试从 '{old.object}' 改为 "
                    f"'{fact.object}' — 拒绝写入"
                )

        if policy == OverridePolicy.APPEND_ONLY and existing_state:
            old = existing_state.get_fact(fact.predicate)
            if old and old.object != fact.object:
                return (
                    f"APPEND_ONLY 字段 '{fact.predicate}' 不允许修改已有值 '{old.object}'"
                )

        return None

    # ── 实体过滤 ──

    def should_ignore(self, entity_name: str) -> bool:
        """检查实体名是否匹配忽略模式列表（临时背景元素等）。"""
        import re as _re
        for pattern in self.ignore_patterns:
            try:
                if _re.search(pattern, entity_name):
                    return True
            except _re.error:
                continue
        return False

    # ── I/O ──

    def to_dict(self) -> dict:
        return {
            "novel": self.novel,
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "generated_by": self.generated_by,
            "ignore_patterns": self.ignore_patterns,
            "override_policy": {
                "locked": self.locked_fields,
                "append_only": self.append_only_fields,
            },
            "retrieval": {
                "min_recall": self.min_recall,
                "max_hop": self.max_hop,
                "max_entity_cards": self.max_entity_cards,
            },
            "entity_schemas": {
                etype: es.to_dict() for etype, es in self.entity_schemas.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> NovelSchema:
        s = cls()
        s.novel = data.get("novel", "")
        s.schema_version = data.get("schema_version", 1)
        s.generated_at = data.get("generated_at", "")
        s.generated_by = data.get("generated_by", "")
        s.ignore_patterns = data.get("ignore_patterns", [])
        op = data.get("override_policy", {})
        s.locked_fields = op.get("locked", [])
        s.append_only_fields = op.get("append_only", [])
        ret = data.get("retrieval", {})
        s.min_recall = ret.get("min_recall", 3)
        s.max_hop = ret.get("max_hop", 1)
        s.max_entity_cards = ret.get("max_entity_cards", 30)
        for etype, edata in data.get("entity_schemas", {}).items():
            s.entity_schemas[etype] = EntitySchema.from_dict(etype, edata)
        return s

    @classmethod
    def load(cls, novel_dir: Path) -> NovelSchema | None:
        """从 novel_schema.json 加载 schema。"""
        path = novel_dir / SCHEMA_FILENAME
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"  [WARN] 加载 novel_schema.json 失败: {e}")
            return None

    def save(self, novel_dir: Path):
        """保存到 novel_schema.json。"""
        path = novel_dir / SCHEMA_FILENAME
        _atomic_write(path, json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

    # ── 向后兼容：无 schema 时的默认行为 ──

    @classmethod
    def default(cls) -> NovelSchema:
        """返回一个宽松的默认 schema（用于尚无 novel_schema.json 的老项目）。"""
        s = cls()
        s.novel = "(default)"
        for etype in ENTITY_TYPES:
            s.entity_schemas[etype] = EntitySchema(entity_type=etype)
        return s


# ═══════════════════════════════════════════════════════════════
# 状态数据类（与 schema 无关，保持不变）
# ═══════════════════════════════════════════════════════════════

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

    def get_active_facts_list(self) -> list[EntityFact]:
        """返回所有当前有效的事实列表。"""
        return [f for f in self.facts if f.until_chapter is None]


@dataclass
class StateDelta:
    """单次蒸馏产出的状态变更 delta。"""
    entity: str
    entity_type: str
    chapter: int
    facts_added: list[EntityFact] = field(default_factory=list)
    facts_retired: list[tuple[str, str]] = field(default_factory=list)  # (predicate, object)


# ═══════════════════════════════════════════════════════════════
# I/O 函数
# ═══════════════════════════════════════════════════════════════

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
    _atomic_write(state_path, json.dumps(state.to_dict(), ensure_ascii=False, indent=2))


def apply_delta_to_state(state: EntityState, delta: StateDelta) -> EntityState:
    """Immutable apply: 将 delta 应用到 EntityState，返回新状态。"""
    new_facts = copy.deepcopy(state.facts)

    for fact in delta.facts_added:
        for old_fact in new_facts:
            if old_fact.predicate == fact.predicate and old_fact.until_chapter is None:
                old_fact.until_chapter = delta.chapter

    for predicate, _object in delta.facts_retired:
        for old_fact in new_facts:
            if old_fact.predicate == predicate and old_fact.until_chapter is None:
                old_fact.until_chapter = delta.chapter

    new_facts.extend(delta.facts_added)

    return EntityState(
        entity=state.entity,
        entity_type=state.entity_type,
        last_updated_chapter=delta.chapter,
        facts=new_facts,
    )


def state_to_markdown_fragment(state: EntityState, schema: NovelSchema | None = None) -> str:
    """从 state.json 生成 markdown 状态摘要（供 prompt 用）。

    如果有 schema，按 schema 定义的 priority 排序；
    否则使用简单字母序。
    """
    active = state.get_all_active_facts()
    if not active:
        return "（无结构化状态记录）"

    lines = []

    if schema:
        # 按 schema 的 priority 排序
        pdefs = schema.get_predicates(state.entity_type)
        ordered = sorted(
            active.items(),
            key=lambda item: pdefs[item[0]].priority if item[0] in pdefs else 99,
        )
    else:
        ordered = list(active.items())

    for pred, val in ordered:
        lines.append(f"- {pred}：{val}")

    return "\n".join(lines)
