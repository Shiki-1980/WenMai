"""确定性验证器 —— 零 LLM 成本的后写检查。

在 LLM 蒸馏之后、state 写入之前运行，提供硬性保障：
  - override_policy: locked/append_only 违规
  - power_progression: 修为晋升合规性
  - technique_known: 功法是否在已知列表中
  - possession_source: 物品来源可追溯
  - temporal_consistency: 时间线一致性

所有检查是纯确定性的（正则匹配、字典查找、数值比较），不调用 LLM。
"""

from typing import Optional

from state_schema import (
    NovelSchema, EntityState, EntityFact, StateDelta,
    OverridePolicy, OOC_DIMENSIONS,
    Violation, ValidationResult,
)


class DeterministicValidators:
    """确定性后写验证器集合。"""

    def __init__(self, schema: NovelSchema | None = None):
        self.schema = schema

    # ── 主入口 ──

    def validate_delta(
        self,
        entity_type: str,
        entity_name: str,
        chapter_number: int,
        facts_added: list[EntityFact],
        current_state: EntityState | None,
        chapter_text: str = "",
    ) -> ValidationResult:
        """对一组新增事实运行所有确定性检查。"""
        violations: list[Violation] = []

        violations.extend(self.check_override_policy(
            entity_type, facts_added, current_state,
        ))
        violations.extend(self.check_power_progression(
            entity_type, entity_name, facts_added, current_state, chapter_number,
        ))
        violations.extend(self.check_technique_known(
            entity_type, entity_name, facts_added, current_state,
        ))
        violations.extend(self.check_possession_source(
            entity_type, entity_name, facts_added, chapter_text,
        ))
        violations.extend(self.check_temporal_consistency(
            entity_type, facts_added, current_state,
        ))

        passed = not any(v.is_blocking() for v in violations)
        summary = (
            f"通过" if passed else
            f"{sum(1 for v in violations if v.is_blocking())} 个阻断性违规"
        )
        return ValidationResult(passed=passed, violations=violations, summary=summary)

    # ── 单项检查 ──

    def check_override_policy(
        self,
        entity_type: str,
        facts_added: list[EntityFact],
        current_state: EntityState | None,
    ) -> list[Violation]:
        """检查 override policy 违规（locked / append_only）。

        这是从"打 log"升级为"返回违规"的关键变更。
        """
        violations = []
        if not self.schema:
            return violations

        for fact in facts_added:
            policy = self.schema.get_override_policy(entity_type, fact.predicate)

            if policy == OverridePolicy.LOCKED and current_state:
                old = current_state.get_fact(fact.predicate)
                if old and old.object != fact.object:
                    violations.append(Violation(
                        dimension="plot",
                        rule="override_locked",
                        severity="critical",
                        entity=current_state.entity,
                        predicate=fact.predicate,
                        old_value=old.object,
                        new_value=fact.object,
                        description=(
                            f"LOCKED 字段 '{fact.predicate}' 尝试从 '{old.object}' "
                            f"改为 '{fact.object}'"
                        ),
                        evidence=fact.evidence,
                    ))

            elif policy == OverridePolicy.APPEND_ONLY and current_state:
                old = current_state.get_fact(fact.predicate)
                if old and old.object != fact.object:
                    # append_only: 新值必须包含旧值（追加语义）
                    if old.object not in fact.object:
                        violations.append(Violation(
                            dimension="plot",
                            rule="override_append_only",
                            severity="critical",
                            entity=current_state.entity,
                            predicate=fact.predicate,
                            old_value=old.object,
                            new_value=fact.object,
                            description=(
                                f"APPEND_ONLY 字段 '{fact.predicate}' 不允许修改已有值 "
                                f"'{old.object[:50]}'"
                            ),
                            evidence=fact.evidence,
                        ))

        return violations

    def check_power_progression(
        self,
        entity_type: str,
        entity_name: str,
        facts_added: list[EntityFact],
        current_state: EntityState | None,
        chapter_number: int,
    ) -> list[Violation]:
        """检查修为/力量晋升是否合规。

        根据 schema 中 PowerSystemDef 的 levels 列表检查：
        - 不能跳级（跳过一个中间等级）
        - 单章突破数不超过 max_advance_per_chapter
        """
        violations = []
        if not self.schema:
            return violations

        es = self.schema.get_entity_schema(entity_type)
        if not es or not es.power_system or not es.power_system.levels:
            return violations

        ps = es.power_system

        # 找到与力量等级相关的谓词（ooc_dimension == "power"）
        power_predicates = [
            p for p in es.predicates.values()
            if p.ooc_dimension == "power"
        ]
        if not power_predicates:
            return violations

        power_pred_names = {p.name for p in power_predicates}

        for fact in facts_added:
            if fact.predicate not in power_pred_names:
                continue

            new_level = ps.get_level(fact.object)
            if not new_level:
                continue  # 未知等级，不阻断（由 schema 进化处理）

            # 检查是否跳级
            if current_state:
                old_fact = current_state.get_fact(fact.predicate)
                if old_fact:
                    old_level = ps.get_level(old_fact.object)
                    if old_level and new_level.rank > old_level.rank + 1:
                        skipped = [
                            lv.name for lv in ps.levels
                            if old_level.rank < lv.rank < new_level.rank
                        ]
                        violations.append(Violation(
                            dimension="power",
                            rule="power_skip_level",
                            severity="warning",
                            entity=entity_name,
                            predicate=fact.predicate,
                            old_value=old_fact.object,
                            new_value=fact.object,
                            description=(
                                f"修为从 '{old_fact.object}' 跳到 '{fact.object}'，"
                                f"跳过了 {skipped}"
                            ),
                            evidence=fact.evidence,
                        ))

            # 检查单章突破数
            advance_count = sum(
                1 for f in facts_added
                if f.predicate in power_pred_names
                and ps.get_level(f.object) is not None
                and current_state
                and current_state.get_fact(f.predicate)
                and ps.get_level(current_state.get_fact(f.predicate).object)
                and ps.get_level(f.object).rank > ps.get_level(current_state.get_fact(f.predicate).object).rank
            )
            if advance_count > ps.max_advance_per_chapter:
                violations.append(Violation(
                    dimension="power",
                    rule="power_too_many_advances",
                    severity="warning",
                    entity=entity_name,
                    predicate=fact.predicate,
                    description=(
                        f"单章 {advance_count} 次突破超过上限 "
                        f"({ps.max_advance_per_chapter})"
                    ),
                    evidence=fact.evidence,
                ))

        return violations

    def check_technique_known(
        self,
        entity_type: str,
        entity_name: str,
        facts_added: list[EntityFact],
        current_state: EntityState | None,
    ) -> list[Violation]:
        """检查新增功法/技能是否该角色已知。

        ooc_dimension == "technique" 的谓词，如果 new_value 不在当前已知列表中，
        且没有 evidence 说明获取来源，则发出 warning。
        """
        violations = []
        if not self.schema:
            return violations

        es = self.schema.get_entity_schema(entity_type)
        if not es:
            return violations

        # 找到 technique 维度的谓词
        tech_pred_names = {
            p.name for p in es.predicates.values()
            if p.ooc_dimension == "technique"
        }

        if not tech_pred_names:
            return violations

        # 收集当前已知的技能/功法
        known_techniques: set[str] = set()
        if current_state:
            for fact_obj in current_state.facts:
                if fact_obj.predicate in tech_pred_names and fact_obj.until_chapter is None:
                    # 尝试解析列表值
                    val = fact_obj.object
                    for item in _split_list_value(val):
                        known_techniques.add(item.strip())

        for fact in facts_added:
            if fact.predicate not in tech_pred_names:
                continue

            new_items = _split_list_value(fact.object)
            for item in new_items:
                item = item.strip()
                if item and item not in known_techniques:
                    # 新增了未知功法，检查是否有 evidence
                    if not fact.evidence:
                        violations.append(Violation(
                            dimension="technique",
                            rule="technique_unknown_source",
                            severity="warning",
                            entity=entity_name,
                            predicate=fact.predicate,
                            new_value=item,
                            description=f"新增功法 '{item}' 但无 evidence 说明习得来源",
                        ))

        return violations

    def check_possession_source(
        self,
        entity_type: str,
        entity_name: str,
        facts_added: list[EntityFact],
        chapter_text: str = "",
    ) -> list[Violation]:
        """检查新增物品是否有可追溯来源。

        ooc_dimension == "asset" 的谓词，新增物品时在正文中搜索关键词。
        """
        violations = []
        if not self.schema or not chapter_text:
            return violations

        es = self.schema.get_entity_schema(entity_type)
        if not es:
            return violations

        asset_pred_names = {
            p.name for p in es.predicates.values()
            if p.ooc_dimension == "asset"
        }

        if not asset_pred_names:
            return violations

        for fact in facts_added:
            if fact.predicate not in asset_pred_names:
                continue

            new_items = _split_list_value(fact.object)
            for item in new_items:
                item = item.strip()
                if not item:
                    continue
                # 在正文中搜索物品名
                if item not in chapter_text:
                    violations.append(Violation(
                        dimension="asset",
                        rule="asset_not_in_text",
                        severity="warning",
                        entity=entity_name,
                        predicate=fact.predicate,
                        new_value=item,
                        description=f"新增物品 '{item}' 但在章节正文中未找到提及",
                    ))

        return violations

    def check_temporal_consistency(
        self,
        entity_type: str,
        facts_added: list[EntityFact],
        current_state: EntityState | None,
    ) -> list[Violation]:
        """检查时间一致性。

        目前检查：同一角色不会同时出现在两个不同地点。
        """
        violations = []
        if not current_state:
            return violations

        # 检查位置类谓词（所在/位置）
        location_preds = {"所在", "位置", "location", "current_location"}
        old_location = None
        new_location = None

        if current_state:
            for pred in location_preds:
                f = current_state.get_fact(pred)
                if f:
                    old_location = f.object
                    break

        for fact in facts_added:
            if fact.predicate in location_preds:
                new_location = fact.object
                break

        # 这个检查比较简单：确保没有同时设置多个不同位置
        locations_found = []
        for fact in facts_added:
            if fact.predicate in location_preds:
                locations_found.append(fact.object)
        if len(set(locations_found)) > 1:
            violations.append(Violation(
                dimension="plot",
                rule="temporal_multi_location",
                severity="warning",
                entity=current_state.entity,
                predicate="所在",
                description=f"同一实体设置了多个不同位置: {locations_found}",
            ))

        return violations


def _split_list_value(value: str) -> list[str]:
    """将列表类型的值拆分为独立条目。

    支持：逗号分隔、顿号分隔、换行分隔。
    """
    if not value:
        return []
    # 尝试按常见分隔符拆分
    items = []
    for sep in ["\n", "、", "，", ","]:
        if sep in value:
            items = [s.strip() for s in value.split(sep) if s.strip()]
            break
    if not items:
        items = [value]
    return items
