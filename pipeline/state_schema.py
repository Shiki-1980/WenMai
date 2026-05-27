"""JSON 状态 schema —— 实体事实的机器权威来源。

Entity facts are the single source of truth for character/item/location state.
Markdown frontmatter is retained as human-readable projection, but all programmatic
reads MUST go through state.json.

The schema is now per-novel: each novel has its own novel_schema.json that defines
entity types, predicates, allowed values, override policies, and markdown templates.
No more hardcoded cultivation realms or predicate lists.

Schema 3.0: OOC-aware constraints with five-dimension validation
  - personality: 人物性格 OOC
  - technique:   功法 OOC
  - power:       能力/力量等级 OOC
  - asset:       资产 OOC
  - plot:        剧情 OOC
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


# ── 常量 ──────────────────────────────────────────────────────
ENTITY_TYPES = ("person", "item", "location", "concept")
SCHEMA_FILENAME = "novel_schema.json"

# OOC 维度常量
OOC_DIMENSIONS = ("personality", "technique", "power", "asset", "plot")
OOC_DIMENSION_LABELS = {
    "personality": "人物性格OOC",
    "technique": "功法OOC",
    "power": "能力/力量OOC",
    "asset": "资产OOC",
    "plot": "剧情OOC",
}


# ── Override Policy ────────────────────────────────────────────

class OverridePolicy(Enum):
    """字段的覆盖策略。"""
    LOCKED = "locked"                  # 一旦设定，任何章节不可覆盖
    APPEND_ONLY = "append_only"        # 只能新增，不能删除或修改已有值
    OVERRIDE_ALLOWED = "override_allowed"  # 章节级可覆盖（默认）


# ═══════════════════════════════════════════════════════════════
# Schema 数据类
# ═══════════════════════════════════════════════════════════════

@dataclass
class PredicateDef:
    """一个谓词的定义（来自 novel_schema.json）。

    Schema 3.0 新增：OOC 维度映射、性格标签、数值边界、前置条件、进化追踪。
    """
    name: str                        # 谓词名 e.g. "修为"
    type: str                        # "enum" | "string" | "list" | "number"
    category: str = ""               # 分组 e.g. "实力", "基础", "能力"
    priority: int = 99               # 显示排序，越小越靠前
    override: OverridePolicy = OverridePolicy.OVERRIDE_ALLOWED
    values: list[str] = field(default_factory=list)   # enum 类型的允许值
    description: str = ""            # 给 LLM 看的说明

    # ── Schema 3.0 新增字段 ──
    ooc_dimension: str = ""          # OOC 维度: personality|technique|power|asset|plot
    personality_tags: list[str] = field(default_factory=list)  # 性格标签 e.g. ["谨慎", "重情义"]
    taboos: list[str] = field(default_factory=list)            # 禁忌 e.g. ["绝不能背叛师门"]
    min_value: float | None = None   # 数值下限
    max_value: float | None = None   # 数值上限
    prerequisites: list[str] = field(default_factory=list)     # 前置条件 e.g. ["金丹期"] -> "元婴期"
    progression_curve: str = ""      # 成长曲线: "linear"|"exponential"|"stepwise"
    is_generated: bool = True        # 是否由 LLM 生成（False=用户手动锁定）
    confidence: float = 1.0          # schema 生成器对此谓词的确信度
    last_verified_chapter: int = 0   # 上次验证此谓词的章节

    def to_dict(self) -> dict:
        d = {
            "type": self.type,
            "category": self.category,
            "priority": self.priority,
            "override": self.override.value,
            "values": self.values,
            "description": self.description,
        }
        # Schema 3.0 字段：只序列化非默认值
        if self.ooc_dimension:
            d["ooc_dimension"] = self.ooc_dimension
        if self.personality_tags:
            d["personality_tags"] = self.personality_tags
        if self.taboos:
            d["taboos"] = self.taboos
        if self.min_value is not None:
            d["min_value"] = self.min_value
        if self.max_value is not None:
            d["max_value"] = self.max_value
        if self.prerequisites:
            d["prerequisites"] = self.prerequisites
        if self.progression_curve:
            d["progression_curve"] = self.progression_curve
        if not self.is_generated:
            d["is_generated"] = False
        if self.confidence != 1.0:
            d["confidence"] = self.confidence
        if self.last_verified_chapter:
            d["last_verified_chapter"] = self.last_verified_chapter
        return d

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "PredicateDef":
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
            # Schema 3.0
            ooc_dimension=data.get("ooc_dimension", ""),
            personality_tags=data.get("personality_tags", []),
            taboos=data.get("taboos", []),
            min_value=data.get("min_value"),
            max_value=data.get("max_value"),
            prerequisites=data.get("prerequisites", []),
            progression_curve=data.get("progression_curve", ""),
            is_generated=data.get("is_generated", True),
            confidence=data.get("confidence", 1.0),
            last_verified_chapter=data.get("last_verified_chapter", 0),
        )


@dataclass
class OOCRule:
    """一条机器可读的 OOC 规则。"""
    dimension: str                    # personality|technique|power|asset|plot
    rule_type: str                    # "prohibition"|"requirement"|"threshold"|"chain"
    predicate: str                    # 关联的谓词
    condition: str                    # 条件表达式（机器可读）
    description: str                  # 人类可读描述
    severity: str = "warning"         # critical|warning|info
    auto_fix: str = ""                # 可选的自动修复策略

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "rule_type": self.rule_type,
            "predicate": self.predicate,
            "condition": self.condition,
            "description": self.description,
            "severity": self.severity,
            "auto_fix": self.auto_fix,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OOCRule":
        return cls(
            dimension=data.get("dimension", ""),
            rule_type=data.get("rule_type", ""),
            predicate=data.get("predicate", ""),
            condition=data.get("condition", ""),
            description=data.get("description", ""),
            severity=data.get("severity", "warning"),
            auto_fix=data.get("auto_fix", ""),
        )


@dataclass
class PowerLevel:
    """力量体系中的一个等级。"""
    name: str                         # e.g. "金丹期"
    rank: int                         # 数值排名（越大越强）
    power_ceiling: str = ""           # 物理上限描述 e.g. "碎山"
    requires: list[str] = field(default_factory=list)  # 前置条件

    def to_dict(self) -> dict:
        d = {"name": self.name, "rank": self.rank}
        if self.power_ceiling:
            d["power_ceiling"] = self.power_ceiling
        if self.requires:
            d["requires"] = self.requires
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "PowerLevel":
        return cls(
            name=data.get("name", ""),
            rank=data.get("rank", 0),
            power_ceiling=data.get("power_ceiling", ""),
            requires=data.get("requires", []),
        )


@dataclass
class PowerSystemDef:
    """力量体系定义 —— 用于力量等级 OOC 检查。"""
    name: str = ""
    levels: list[PowerLevel] = field(default_factory=list)
    advancement_rules: list[str] = field(default_factory=list)
    max_advance_per_chapter: int = 1
    min_chapters_between_advance: int = 2

    def get_level(self, name: str) -> PowerLevel | None:
        for lv in self.levels:
            if lv.name == name:
                return lv
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "levels": [lv.to_dict() for lv in self.levels],
            "advancement_rules": self.advancement_rules,
            "max_advance_per_chapter": self.max_advance_per_chapter,
            "min_chapters_between_advance": self.min_chapters_between_advance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PowerSystemDef":
        return cls(
            name=data.get("name", ""),
            levels=[PowerLevel.from_dict(lv) for lv in data.get("levels", [])],
            advancement_rules=data.get("advancement_rules", []),
            max_advance_per_chapter=data.get("max_advance_per_chapter", 1),
            min_chapters_between_advance=data.get("min_chapters_between_advance", 2),
        )


@dataclass
class SchemaEvolution:
    """一次 schema 进化记录。"""
    chapter: int
    evolution_type: str              # predicate_added|value_extended|constraint_softened|constraint_hardened
    entity_type: str = ""
    predicate: str = ""
    old_value: str = ""
    new_value: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "chapter": self.chapter,
            "evolution_type": self.evolution_type,
            "entity_type": self.entity_type,
            "predicate": self.predicate,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SchemaEvolution":
        return cls(
            chapter=data.get("chapter", 0),
            evolution_type=data.get("evolution_type", ""),
            entity_type=data.get("entity_type", ""),
            predicate=data.get("predicate", ""),
            old_value=data.get("old_value", ""),
            new_value=data.get("new_value", ""),
            reason=data.get("reason", ""),
        )


@dataclass
class Violation:
    """一条验证违规记录。"""
    dimension: str                    # OOC 维度
    rule: str                         # 违规规则名
    severity: str                     # critical|warning|info
    entity: str = ""
    predicate: str = ""
    old_value: str = ""
    new_value: str = ""
    description: str = ""
    evidence: str = ""                # 从章节正文引用的证据
    auto_fix: str = ""

    def is_blocking(self) -> bool:
        return self.severity == "critical"

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "rule": self.rule,
            "severity": self.severity,
            "entity": self.entity,
            "predicate": self.predicate,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "description": self.description,
            "evidence": self.evidence,
            "auto_fix": self.auto_fix,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Violation":
        return cls(
            dimension=data.get("dimension", ""),
            rule=data.get("rule", ""),
            severity=data.get("severity", "warning"),
            entity=data.get("entity", ""),
            predicate=data.get("predicate", ""),
            old_value=data.get("old_value", ""),
            new_value=data.get("new_value", ""),
            description=data.get("description", ""),
            evidence=data.get("evidence", ""),
            auto_fix=data.get("auto_fix", ""),
        )


@dataclass
class ValidationResult:
    """一次验证的完整结果。"""
    passed: bool
    violations: list[Violation] = field(default_factory=list)
    summary: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
            "summary": self.summary,
        }


@dataclass
class EntitySchema:
    """一种实体类型的 schema 定义。"""
    entity_type: str                 # "person" | "item" | "location" | "concept"
    label: str = ""                  # 中文标签 e.g. "人物"
    predicates: dict[str, PredicateDef] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    markdown_template: str = ""      # JSON → Markdown 渲染模板
    ooc_rules: list[OOCRule] = field(default_factory=list)  # Schema 3.0
    power_system: PowerSystemDef | None = None                # Schema 3.0

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

    def locked_predicates(self) -> list[str]:
        """返回所有 LOCKED 策略的谓词名。"""
        return [name for name, p in self.predicates.items() if p.override == OverridePolicy.LOCKED]

    def append_only_predicates(self) -> list[str]:
        """返回所有 APPEND_ONLY 策略的谓词名。"""
        return [name for name, p in self.predicates.items() if p.override == OverridePolicy.APPEND_ONLY]

    def enum_predicates(self) -> dict[str, list[str]]:
        """返回所有 enum 类型谓词及其允许值。"""
        return {name: p.values for name, p in self.predicates.items() if p.type == "enum" and p.values}

    def to_dict(self) -> dict:
        d = {
            "label": self.label,
            "predicates": {name: p.to_dict() for name, p in self.predicates.items()},
            "tags": self.tags,
            "markdown_template": self.markdown_template,
        }
        if self.ooc_rules:
            d["ooc_rules"] = [r.to_dict() for r in self.ooc_rules]
        if self.power_system:
            d["power_system"] = self.power_system.to_dict()
        return d

    @classmethod
    def from_dict(cls, entity_type: str, data: dict) -> "EntitySchema":
        predicates = {}
        for name, pdata in data.get("predicates", {}).items():
            predicates[name] = PredicateDef.from_dict(name, pdata)
        ooc_rules = [OOCRule.from_dict(r) for r in data.get("ooc_rules", [])]
        ps_data = data.get("power_system")
        power_system = PowerSystemDef.from_dict(ps_data) if ps_data else None
        return cls(
            entity_type=entity_type,
            label=data.get("label", ""),
            predicates=predicates,
            tags=data.get("tags", []),
            markdown_template=data.get("markdown_template", ""),
            ooc_rules=ooc_rules,
            power_system=power_system,
        )


class NovelSchema:
    """从 novel_schema.json 加载的整本小说 schema。

    Schema 3.0: 移除全局 locked_fields/append_only_fields（死代码），
    替代为 per-predicate override policy + OOC 维度支持。

    使用方式:
        schema = NovelSchema.load(Path("novels/万古劫烬"))
        person_preds = schema.get_predicates("person")
        result = schema.validate_fact(fact, entity_type)
    """

    def __init__(self):
        self.novel: str = ""
        self.schema_version: int = 3
        self.generated_at: str = ""
        self.generated_by: str = ""
        self.entity_schemas: dict[str, EntitySchema] = {}

        # 检索配置
        self.min_recall: int = 3
        self.max_hop: int = 1
        self.max_entity_cards: int = 30

        # ── Schema 3.0 新增 ──
        self.evolution_history: list[SchemaEvolution] = []
        self.last_evolved_chapter: int = 0
        self.protagonist_personality: dict[str, str] = {}     # {"核心动机": "...", "行为逻辑": "..."}
        self.validator_threshold: str = "warning"              # critical|warning|info
        self.max_review_iterations: int = 3

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

    def get_entity_schema_summary(self, entity_type: str) -> str:
        """生成实体 schema 的人类可读摘要（用于注入 prompt）。"""
        es = self.get_entity_schema(entity_type)
        if not es:
            return ""

        lines = [f"## {es.label or entity_type} 谓词定义"]
        for p in es.predicates_sorted():
            extras = []
            if p.override == OverridePolicy.LOCKED:
                extras.append("🔒锁定")
            elif p.override == OverridePolicy.APPEND_ONLY:
                extras.append("➕仅追加")
            if p.type == "enum" and p.values:
                extras.append(f"允许值: {', '.join(p.values[:8])}")
            if p.ooc_dimension:
                extras.append(f"OOC维度: {OOC_DIMENSION_LABELS.get(p.ooc_dimension, p.ooc_dimension)}")
            extra_str = f" ({'; '.join(extras)})" if extras else ""
            lines.append(f"- {p.name}({p.type}){extra_str}: {p.description or '(无描述)'}")
        return "\n".join(lines)

    def get_ooc_constraints_for_entity(self, entity_type: str, entity_name: str) -> str:
        """获取某个实体的 OOC 约束文本（用于注入写作提示）。"""
        es = self.get_entity_schema(entity_type)
        if not es:
            return ""

        parts = []
        # 锁定字段
        locked = es.locked_predicates()
        if locked:
            parts.append(f"**锁定字段（不可修改）**: {', '.join(locked)}")

        # 仅追加字段
        append_only = es.append_only_predicates()
        if append_only:
            parts.append(f"**仅追加字段**: {', '.join(append_only)}")

        # 枚举字段及允许值
        enums = es.enum_predicates()
        if enums:
            for pname, pvals in enums.items():
                parts.append(f"**{pname}** 允许值: {', '.join(pvals[:10])}")

        # 性格标签
        for p in es.predicates.values():
            if p.personality_tags:
                parts.append(f"**{p.name}性格标签**: {', '.join(p.personality_tags)}")
            if p.taboos:
                parts.append(f"**{p.name}禁忌**: {', '.join(p.taboos)}")

        return "\n".join(parts) if parts else ""

    def get_locked_predicates(self, entity_type: str) -> list[str]:
        """返回某实体类型的所有 LOCKED 谓词名。"""
        es = self.get_entity_schema(entity_type)
        return es.locked_predicates() if es else []

    def get_append_only_predicates(self, entity_type: str) -> list[str]:
        """返回某实体类型的所有 APPEND_ONLY 谓词名。"""
        es = self.get_entity_schema(entity_type)
        return es.append_only_predicates() if es else []

    def get_enum_predicates(self, entity_type: str) -> dict[str, list[str]]:
        """返回某实体类型的所有 enum 谓词及其允许值。"""
        es = self.get_entity_schema(entity_type)
        return es.enum_predicates() if es else {}

    # ── 校验 ──

    def validate_fact(self, fact: "EntityFact", entity_type: str) -> list[str]:
        """校验单条事实，返回错误列表（空 = 通过）。

        Schema 3.0: 未定义的谓词不再静默通过，而是记录为 info 级别（不阻断）。
        enum 检查变为 soft warning，不阻断——因为 schema 枚举天然不完整。
        """
        errors = []

        if not fact.predicate or not fact.predicate.strip():
            errors.append("事实谓词为空")
            return errors
        if not fact.object or not fact.object.strip():
            errors.append(f"'{fact.predicate}' 的值不能为空")
        if not isinstance(fact.since_chapter, int) or fact.since_chapter < 1:
            errors.append(f"since_chapter 必须是正整数，得到 {fact.since_chapter}")
        if fact.until_chapter is not None:
            if not isinstance(fact.until_chapter, int) or fact.until_chapter <= fact.since_chapter:
                errors.append(f"until_chapter ({fact.until_chapter}) 必须 > since_chapter ({fact.since_chapter})")

        # enum 类型检查：现在是 soft warning，不阻断
        # 因为 LLM 生成的枚举列表天然不完整，硬阻断会拒绝合理的值
        pdef = self.get_predicate_def(entity_type, fact.predicate)
        if pdef is not None and pdef.type == "enum" and pdef.values:
            if fact.object not in pdef.values:
                # 标记为 info 级别，提示可能需要扩展 schema
                errors.append(
                    f"[info] '{fact.predicate}' 的值 '{fact.object}' 不在 schema 枚举中 "
                    f"({', '.join(pdef.values[:5])}...)，可能需要扩展 schema"
                )

        return errors

    def validate_delta(self, delta: "StateDelta", known_entities: set[str]) -> list[str]:
        """校验整个 delta。"""
        errors = []
        if delta.entity not in known_entities:
            errors.append(f"实体 '{delta.entity}' 不在已知实体列表中")
        if delta.entity_type not in ENTITY_TYPES:
            errors.append(f"实体类型 '{delta.entity_type}' 不合法，允许: {ENTITY_TYPES}")
        for fact in delta.facts_added:
            errors.extend(self.validate_fact(fact, delta.entity_type))
        return errors

    def check_override_violation(
        self, entity_type: str, predicate: str,
        new_value: str, existing_state: "EntityState | None",
    ) -> str | None:
        """检查一条新事实是否违反 override policy。

        返回错误描述，或 None（无违反）。

        Schema 3.0: 第一个参数改为 entity_type（之前从 fact 推断）。
        """
        policy = self.get_override_policy(entity_type, predicate)

        if policy == OverridePolicy.LOCKED and existing_state:
            old = existing_state.get_fact(predicate)
            if old and old.object != new_value:
                return (
                    f"LOCKED 字段 '{predicate}' 尝试从 '{old.object}' 改为 "
                    f"'{new_value}' — 拒绝写入"
                )

        if policy == OverridePolicy.APPEND_ONLY and existing_state:
            old = existing_state.get_fact(predicate)
            if old and old.object != new_value:
                return (
                    f"APPEND_ONLY 字段 '{predicate}' 不允许修改已有值 '{old.object}'"
                )

        return None

    # ── I/O ──

    def to_dict(self) -> dict:
        d = {
            "novel": self.novel,
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "generated_by": self.generated_by,
            "retrieval": {
                "min_recall": self.min_recall,
                "max_hop": self.max_hop,
                "max_entity_cards": self.max_entity_cards,
            },
            "entity_schemas": {
                etype: es.to_dict() for etype, es in self.entity_schemas.items()
            },
        }
        # Schema 3.0 字段
        if self.evolution_history:
            d["evolution_history"] = [e.to_dict() for e in self.evolution_history]
        if self.last_evolved_chapter:
            d["last_evolved_chapter"] = self.last_evolved_chapter
        if self.protagonist_personality:
            d["protagonist_personality"] = self.protagonist_personality
        if self.validator_threshold != "warning":
            d["validator_threshold"] = self.validator_threshold
        if self.max_review_iterations != 3:
            d["max_review_iterations"] = self.max_review_iterations
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "NovelSchema":
        s = cls()
        s.novel = data.get("novel", "")
        s.schema_version = data.get("schema_version", 1)
        s.generated_at = data.get("generated_at", "")
        s.generated_by = data.get("generated_by", "")
        ret = data.get("retrieval", {})
        s.min_recall = ret.get("min_recall", 3)
        s.max_hop = ret.get("max_hop", 1)
        s.max_entity_cards = ret.get("max_entity_cards", 30)
        for etype, edata in data.get("entity_schemas", {}).items():
            s.entity_schemas[etype] = EntitySchema.from_dict(etype, edata)
        # Schema 3.0
        s.evolution_history = [
            SchemaEvolution.from_dict(e) for e in data.get("evolution_history", [])
        ]
        s.last_evolved_chapter = data.get("last_evolved_chapter", 0)
        s.protagonist_personality = data.get("protagonist_personality", {})
        s.validator_threshold = data.get("validator_threshold", "warning")
        s.max_review_iterations = data.get("max_review_iterations", 3)
        return s

    @classmethod
    def load(cls, novel_dir: Path) -> "NovelSchema | None":
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
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            "utf-8",
        )

    # ── 向后兼容：无 schema 时的默认行为 ──

    @classmethod
    def default(cls) -> "NovelSchema":
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
    entity_grade: str = "stub" # reference | stub | active
    first_seen_chapter: int = 0
    facts: list[EntityFact] = field(default_factory=list)

    @property
    def is_reference(self) -> bool:
        return self.entity_grade == "reference"

    @property
    def is_stub(self) -> bool:
        return self.entity_grade == "stub"

    @property
    def is_active(self) -> bool:
        return self.entity_grade == "active"

    def to_dict(self) -> dict:
        d = {
            "entity": self.entity,
            "entity_type": self.entity_type,
            "last_updated_chapter": self.last_updated_chapter,
            "facts": [f.to_dict() for f in self.facts],
        }
        if self.entity_grade != "stub":
            d["entity_grade"] = self.entity_grade
        if self.first_seen_chapter:
            d["first_seen_chapter"] = self.first_seen_chapter
        return d

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
            entity_grade=data.get("entity_grade", "stub"),
            first_seen_chapter=data.get("first_seen_chapter", 0),
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
