"""蒸馏器 —— 三阶段流水线：Observer → Settler → State Validator。

Observer (LLM, temp 0.6): 自由文本观察，过度提取事实变化
Settler (LLM, temp 0.25): 将观察转化为结构化 JSON delta
State Validator (LLM, temp 0.15): 比较新旧状态，检测矛盾
  → 失败时自动重试 Settler 1 次
  → 仍失败 → state-degraded（保存正文但不更新状态）
"""


class DistillResult:
    """蒸馏结果。"""
    def __init__(self, data: dict, degraded: bool = False):
        self.data = data
        self.degraded = degraded  # True = 状态更新失败，正文已保存但状态回退

    def __getitem__(self, key):
        return self.data.get(key)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __bool__(self):
        return bool(self.data)


class ChapterDistiller:
    def __init__(self, generator, reader, schema=None):
        self.generator = generator
        self.reader = reader
        self.schema = schema

    # ── 公开接口 ──────────────────────────────────────────────

    def distill(self, chapter_number: int, chapter_text: str) -> DistillResult:
        """完整三阶段蒸馏流水线。

        返回 DistillResult，degraded=True 表示状态更新失败。
        """
        known = self.reader.all_entity_names()
        known_names = [f"[{t}] {n}" for t, n in known]

        # 获取当前状态摘要
        current_states = self._get_current_states()

        # 构建 schema 提示（供 observer/settler/validator 使用）
        schema_hint = self._build_entity_schema_hint()
        constraints_hint = self._build_schema_constraints_hint()
        schema_context = self._build_validator_schema_context()

        # 阶段 1: Observer —— 过度提取
        observations = self.generator.observe(
            chapter_text, known_names, current_states,
            schema_hint=schema_hint,
        )
        if not observations:
            return DistillResult({}, degraded=False)

        # 阶段 2: Settler —— 输出 JSON delta（首次尝试）
        delta = self.generator.settle(
            observations, known_names, current_states,
            schema_constraints=constraints_hint,
        )
        if not delta:
            return DistillResult({}, degraded=False)

        # 阶段 3: State Validator —— 校验
        old_state = self._get_old_state_snapshot()
        new_state = self._simulate_new_state(delta, chapter_number)

        validation = self.generator.validate_state(
            chapter_text[:2000], observations, old_state, new_state,
            schema_context=schema_context,
        )

        if validation.get("passed", True):
            return DistillResult(
                self._build_result(chapter_number, delta, observations),
                degraded=False,
            )

        # 校验失败 → 重试 Settler
        issues = validation.get("issues", [])
        print(f"  [WARN] 状态校验失败，重试 Settler...")
        print(f"    问题: {'; '.join(i.get('description', '')[:60] for i in issues)}")

        delta_retry = self.generator.settle(
            observations, known_names, current_states,
            retry_hint=_format_issues(issues),
            schema_constraints=constraints_hint,
        )

        if delta_retry:
            new_state_retry = self._simulate_new_state(delta_retry, chapter_number)
            validation_retry = self.generator.validate_state(
                chapter_text[:2000], observations, old_state, new_state_retry,
                schema_context=schema_context,
            )
            if validation_retry.get("passed", True):
                print(f"  -> Settler 重试成功")
                return DistillResult(
                    self._build_result(chapter_number, delta_retry, observations),
                    degraded=False,
                )

        print(f"  [ERROR] 状态校验重试仍失败，进入 state-degraded 模式")
        print(f"    正文已保存，但实体状态未更新。请手动 review。")
        return DistillResult(
            self._build_result_degraded(chapter_number, observations, issues),
            degraded=True,
        )

    # ── 内部方法 ──────────────────────────────────────────────

    def _get_current_states(self) -> str:
        """获取所有实体当前状态的文本摘要。"""
        lines = []
        for etype, name in self.reader.all_entity_names():
            state = self.reader.read_entity_state(etype, name)
            if state and state.facts:
                active = state.get_all_active_facts()
                summary = ", ".join(f"{k}={v}" for k, v in active.items())
                lines.append(f"[{etype}] {name}: {summary}")
            else:
                lines.append(f"[{etype}] {name}: 无状态记录")
        return "\n".join(lines)

    def _load_schema(self):
        """惰性加载 schema。"""
        if self.schema:
            return self.schema
        from state_schema import NovelSchema
        try:
            self.schema = NovelSchema.load(self.reader.root)
        except Exception:
            pass
        return self.schema

    def _build_entity_schema_hint(self) -> str:
        """为 Observer 构建实体类型→谓词语义映射指南。"""
        schema = self._load_schema()
        if not schema:
            return ""
        hints = []
        for etype in ["person", "item", "location", "concept"]:
            preds = schema.get_predicates(etype)
            if not preds:
                continue
            pred_list = ", ".join(
                f"{name}({p.type})" if p.category else name
                for name, p in sorted(preds.items(), key=lambda x: x[1].priority)
            )
            es = schema.get_entity_schema(etype)
            label = es.label if es else etype
            hints.append(f"- {etype} ({label}): {pred_list}")
        return "\n".join(hints)

    def _build_schema_constraints_hint(self) -> str:
        """为 Settler 构建 schema 约束提示。"""
        schema = self._load_schema()
        if not schema:
            return "（无 schema 约束，所有字段均可自由修改）"

        lines = []
        for etype in ["person", "item", "location", "concept"]:
            locked = schema.get_locked_predicates(etype)
            append_only = schema.get_append_only_predicates(etype)
            enums = schema.get_enum_predicates(etype)

            if locked or append_only or enums:
                es = schema.get_entity_schema(etype)
                label = es.label if es else etype
                lines.append(f"### {label} ({etype})")

            if locked:
                lines.append(f"- LOCKED（不可修改）: {', '.join(locked)}")
            if append_only:
                lines.append(f"- APPEND_ONLY（只能追加）: {', '.join(append_only)}")
            if enums:
                for pname, pvals in enums.items():
                    lines.append(f"- {pname} 允许值: {', '.join(pvals[:8])}")

        return "\n".join(lines) if lines else "（无 schema 约束）"

    def _build_validator_schema_context(self) -> str:
        """为 State Validator 构建 schema 上下文（5 维度 OOC）。"""
        schema = self._load_schema()
        if not schema:
            return "（无 schema，仅做通用一致性检查）"

        lines = []
        # 力量体系
        for etype in ["person"]:
            es = schema.get_entity_schema(etype)
            if es and es.power_system and es.power_system.levels:
                ps = es.power_system
                levels_str = " → ".join(lv.name for lv in sorted(ps.levels, key=lambda x: x.rank))
                lines.append(f"力量体系: {ps.name or '修为'}")
                lines.append(f"等级序列: {levels_str}")
                lines.append(f"单章最大突破: {ps.max_advance_per_chapter} 级")

        # 每个实体类型的 OOC 规则
        for etype in ["person", "item", "location", "concept"]:
            es = schema.get_entity_schema(etype)
            if not es:
                continue
            # 收集所有有性格标签的谓词
            for p in es.predicates.values():
                if p.personality_tags:
                    lines.append(f"{es.label or etype}.{p.name} 性格标签: {', '.join(p.personality_tags)}")
                if p.taboos:
                    lines.append(f"{es.label or etype}.{p.name} 禁忌: {', '.join(p.taboos)}")

        return "\n".join(lines) if lines else ""

    def _get_old_state_snapshot(self) -> str:
        """获取旧状态快照（供 Validator 比较）。"""
        parts = []
        for etype, name in self.reader.all_entity_names():
            state = self.reader.read_entity_state(etype, name)
            if state:
                active = state.get_all_active_facts()
                if active:
                    parts.append(f"[{etype}] {name}:")
                    for k, v in active.items():
                        parts.append(f"  {k}: {v}")
        return "\n".join(parts)

    def _simulate_new_state(self, delta: dict, chapter_number: int) -> str:
        """模拟应用 delta 后的新状态（纯文本，给 Validator 看）。"""
        lines = []
        for ent_delta in delta.get("entity_deltas", []):
            name = ent_delta.get("entity", "")
            etype = ent_delta.get("entity_type", "person")
            lines.append(f"[{etype}] {name}:")
            for pred, change in ent_delta.get("changes", {}).items():
                action = change.get("action", "change")
                new_val = change.get("new_value", "")
                old_val = change.get("old_value", "")
                if action == "change":
                    lines.append(f"  {pred}: {old_val} → {new_val}")
                elif action in ("add", "append", "append_description"):
                    lines.append(f"  {pred}: +{new_val}")
                elif action == "remove":
                    lines.append(f"  {pred}: -{old_val}")
        return "\n".join(lines)

    def _build_result(self, chapter_number: int, delta: dict, observations: str) -> dict:
        """构建完整蒸馏结果。"""
        # 保留旧格式兼容性
        entity_updates = []
        for ent_delta in delta.get("entity_deltas", []):
            for pred, change in ent_delta.get("changes", {}).items():
                if change.get("action") in ("change", "add", "append", "append_description"):
                    entity_updates.append({
                        "entity": ent_delta["entity"],
                        "field": pred,
                        "new_value": change.get("new_value", ""),
                        "old_value": change.get("old_value", ""),
                        "evidence": change.get("evidence", ""),
                    })

        return {
            "summary_meta": {
                "chapter": chapter_number,
                "entities_present": delta.get("entities_present", []),
                "new_entities": delta.get("new_entities", []),
                "revealed_plots": delta.get("revealed_plots", []),
                "new_plots": delta.get("new_plots", []),
                "plots_advanced": delta.get("plots_advanced", []),
                "keywords": delta.get("keywords", []),
                "key_residue": delta.get("key_residue", ""),
            },
            "summary_body": (
                f"## 摘要\n{delta.get('summary', '')}\n\n"
                f"## 关键残留\n{delta.get('key_residue', '')}"
            ),
            "entity_updates": entity_updates,
            "entity_deltas": self._normalize_deltas(delta.get("entity_deltas", [])),
            "new_entities": delta.get("new_entities", []),
            "new_plots": delta.get("new_plots", []),
            "revealed_plots": delta.get("revealed_plots", []),
            "plots_advanced": delta.get("plots_advanced", []),
            "observations": observations,
            "index_updates": self._build_index_updates(chapter_number, delta),
            "degraded": False,
        }

    def _build_result_degraded(self, chapter_number: int, observations: str, issues: list) -> dict:
        """构建 state-degraded 结果——正文保存但状态不更新。"""
        return {
            "summary_meta": {
                "chapter": chapter_number,
                "entities_present": [],
                "new_entities": [],
                "keywords": [],
                "key_residue": "",
                "state_degraded": True,
            },
            "summary_body": (
                f"## 注意：本章状态更新失败（state-degraded）\n\n"
                f"观察报告：\n{observations[:500]}\n\n"
                f"校验问题：\n" + "\n".join(
                    f"- {i.get('description', '')}" for i in issues
                )
            ),
            "entity_updates": [],
            "entity_deltas": [],
            "new_entities": [],
            "new_plots": [],
            "revealed_plots": [],
            "plots_advanced": [],
            "observations": observations,
            "index_updates": {},
            "validation_issues": issues,
            "degraded": True,
        }

    # 关系反向映射：当 A 对 B 有某种关系时，B 对 A 的对应关系
    RELATION_INVERSE = {
        "信任": "被信任",
        "敌视": "被敌视",
        "爱慕": "被爱慕",
        "尊敬": "被尊敬",
        "仰慕": "被仰慕",
        "追随": "被追随",
        "效忠": "被效忠",
        "崇拜": "被崇拜",
        "仇恨": "被仇恨",
        "畏惧": "被畏惧",
        "感激": "被感激",
        "利用": "被利用",
        "依赖": "依赖",
        "恋人": "恋人",
        "朋友": "朋友",
        "合作": "合作",
        "对手": "对手",
        "师徒": "师徒",
    }

    @classmethod
    def _infer_reverse_relations(cls, entity_deltas: list, known_names: set[str],
                                  entity_types: dict[str, str]) -> list:
        """为关系类事实推导反向关系 delta。

        当 A 的关系字段出现 "B: 信任" 时，自动为 B 生成 "A: 被信任"。
        不会覆盖 B 的已有关系事实，只是追加新的。

        Returns:
            需要追加的反向 delta 列表（与 _normalize_deltas 输出格式相同）
        """
        reverse_deltas = []
        for ent_delta in entity_deltas:
            source_entity = ent_delta.get("entity", "")
            source_type = ent_delta.get("entity_type", "person")
            for fact in ent_delta.get("facts", []):
                predicate = fact.get("predicate", "")
                if predicate not in ("关系", "relation", "relationships"):
                    continue
                obj = fact.get("object", "")
                # 格式: "目标实体: 关系描述" 或 "目标实体：关系描述"
                for sep in (": ", "：", ": ", "："):
                    if sep in obj:
                        target_name, rel_desc = obj.split(sep, 1)
                        target_name = target_name.strip()
                        rel_desc = rel_desc.strip()
                        break
                else:
                    continue

                if not target_name or target_name not in known_names:
                    continue

                # 尝试匹配已知关系类型以获取反向描述
                inverse_desc = None
                for rel_key, rel_inv in cls.RELATION_INVERSE.items():
                    if rel_key in rel_desc:
                        inverse_desc = rel_desc.replace(rel_key, rel_inv, 1)
                        break
                if inverse_desc is None:
                    # 无法匹配，用通用的 "被..." 形式
                    inverse_desc = f"被{rel_desc}" if not rel_desc.startswith("被") else rel_desc

                target_type = entity_types.get(target_name, "person")
                reverse_deltas.append({
                    "entity": target_name,
                    "entity_type": target_type,
                    "facts": [{
                        "predicate": predicate,
                        "object": f"{source_entity}: {inverse_desc}",
                        "action": "append",
                        "evidence": fact.get("evidence", ""),
                    }],
                })

        return reverse_deltas

    def _normalize_deltas(self, entity_deltas: list) -> list:
        """将 Settler 的 changes dict 转换为 writer 的 facts 数组格式。

        Settler 输出: {"entity": "陆沉", "changes": {"修为": {"action": "change", ...}}}
        Writer 期望: {"entity": "陆沉", "entity_type": "person", "facts": [{"predicate": "修为", ...}]}

        同时为关系类变化推导反向关系 delta。
        """
        normalized = []
        for ent_delta in entity_deltas:
            entity_name = ent_delta.get("entity", "")
            entity_type = ent_delta.get("entity_type", "person")
            changes = ent_delta.get("changes", {})

            facts = []
            for pred, change in changes.items():
                action = change.get("action", "change")
                facts.append({
                    "predicate": pred,
                    "object": change.get("new_value", ""),
                    "old_value": change.get("old_value", ""),
                    "action": action,
                    "evidence": change.get("evidence", ""),
                })

            if facts:
                normalized.append({
                    "entity": entity_name,
                    "entity_type": entity_type,
                    "facts": facts,
                })

        # 推导反向关系
        known_names = set()
        entity_types = {}
        for etype, name in self.reader.all_entity_names():
            known_names.add(name)
            entity_types[name] = etype

        reverse = self._infer_reverse_relations(normalized, known_names, entity_types)
        if reverse:
            normalized.extend(reverse)
            names = [r["entity"] for r in reverse]
            print(f"  -> 双向关系推导: {', '.join(names)}")

        return normalized

    def _build_index_updates(self, chapter_number: int, delta: dict) -> dict:
        """构建倒排索引更新。"""
        updates = {}
        for name in delta.get("entities_present", []):
            if name and isinstance(name, str):
                idx = self.reader.read_entity_index() or {"entities": {}}
                existing = idx.get("entities", {}).get(name, {}).get("chapters", [])
                if chapter_number not in existing:
                    existing.append(chapter_number)
                updates[name] = existing
        return updates


def _format_issues(issues: list) -> str:
    """格式化校验问题为 Settler 重试提示。"""
    lines = ["## 上次结算被状态校验拒绝，以下问题需要修复："]
    for i in issues:
        lines.append(f"- [{i.get('severity', '?')}] {i.get('description', '')}")
        if i.get("suggestion"):
            lines.append(f"  修复建议: {i['suggestion']}")
    return "\n".join(lines)
