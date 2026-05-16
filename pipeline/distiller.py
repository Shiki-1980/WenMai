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
    def __init__(self, generator, reader):
        self.generator = generator
        self.reader = reader

    # ── 公开接口 ──────────────────────────────────────────────

    def distill(self, chapter_number: int, chapter_text: str) -> DistillResult:
        """完整三阶段蒸馏流水线。

        返回 DistillResult，degraded=True 表示状态更新失败。
        """
        known = self.reader.all_entity_names()
        known_names = [f"[{t}] {n}" for t, n in known]

        # 获取当前状态摘要
        current_states = self._get_current_states()

        # 阶段 1: Observer —— 过度提取
        observations = self.generator.observe(
            chapter_text, known_names, current_states
        )
        if not observations:
            return DistillResult({}, degraded=False)

        # 阶段 2: Settler —— 输出 JSON delta（首次尝试）
        delta = self.generator.settle(
            observations, known_names, current_states
        )
        if not delta:
            return DistillResult({}, degraded=False)

        # 阶段 3: State Validator —— 校验
        old_state = self._get_old_state_snapshot()
        new_state = self._simulate_new_state(delta, chapter_number)

        validation = self.generator.validate_state(
            chapter_text[:2000], observations, old_state, new_state
        )

        if validation.get("passed", True):
            # 校验通过，组装完整结果
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
        )

        if delta_retry:
            # 重试后再次校验
            new_state_retry = self._simulate_new_state(delta_retry, chapter_number)
            validation_retry = self.generator.validate_state(
                chapter_text[:2000], observations, old_state, new_state_retry
            )
            if validation_retry.get("passed", True):
                print(f"  -> Settler 重试成功")
                return DistillResult(
                    self._build_result(chapter_number, delta_retry, observations),
                    degraded=False,
                )

        # 重试仍失败 → state-degraded
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
            "observations": observations,
            "index_updates": {},
            "validation_issues": issues,
            "degraded": True,
        }

    def _normalize_deltas(self, entity_deltas: list) -> list:
        """将 Settler 的 changes dict 转换为 writer 的 facts 数组格式。

        Settler 输出: {"entity": "陆沉", "changes": {"修为": {"action": "change", ...}}}
        Writer 期望: {"entity": "陆沉", "entity_type": "person", "facts": [{"predicate": "修为", ...}]}
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
