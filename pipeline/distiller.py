"""蒸馏器 —— 从章节中提取结构化信息，生成 JSON delta 更新实体状态。"""

import json
from datetime import datetime


class ChapterDistiller:
    def __init__(self, generator, reader):
        self.generator = generator
        self.reader = reader

    def distill(self, chapter_number: int, chapter_text: str) -> dict:
        """
        蒸馏一章，返回结构化结果（兼容旧格式 + 新增 entity_deltas）。

        {
          "summary_meta": dict,        # 章节摘要的 frontmatter
          "summary_body": str,         # 章节摘要正文
          "entity_updates": [...],     # 旧格式：entity/field/new_value/old_value (向后兼容)
          "entity_deltas": [...],      # 新格式：entity/entity_type/facts[{predicate, object, old_value, evidence}]
          "new_entities": [...],       # 需要创建的新实体
          "new_plots": [...],          # 新伏笔
          "revealed_plots": [...],     # 已回收伏笔
          "index_updates": {...},      # 倒排索引更新
        }
        """
        # 获取已知实体列表
        known = self.reader.all_entity_names()
        known_names = [f"[{t}] {n}" for t, n in known]

        # 调用 LLM 蒸馏
        raw = self.generator.distill_chapter(
            chapter_text, ", ".join(known_names)
        )
        if not raw:
            return {}

        return self._process_distill_result(chapter_number, raw)

    def _process_distill_result(self, chapter_number: int, raw: dict) -> dict:
        """将 LLM 蒸馏结果转化为可执行的操作指令。"""
        # 章节摘要的 frontmatter（保留旧格式兼容）
        summary_meta = {
            "chapter": chapter_number,
            "entities_present": raw.get("entities_present", []),
            "status_changes": raw.get("status_changes", []),  # 旧格式保留
            "new_entities": raw.get("new_entities", []),
            "revealed_plots": raw.get("revealed_plots", []),
            "new_plots": raw.get("new_plots", []),
            "keywords": raw.get("keywords", []),
            "key_residue": raw.get("key_residue", ""),
        }

        # ── 新格式: entity_deltas（给 state.json 用）──
        entity_deltas = raw.get("entity_deltas", [])

        # ── 旧格式兼容: 从 entity_deltas 生成 entity_updates ──
        entity_updates = self._extract_legacy_updates(entity_deltas)

        # 如果 LLM 仍用旧 status_changes 格式，转换为 entity_deltas
        if not entity_deltas and raw.get("status_changes"):
            entity_deltas = self._convert_legacy_to_deltas(
                raw.get("status_changes", []), chapter_number
            )
            entity_updates = raw.get("status_changes", [])

        # ── 新实体 ──
        new_entities = []
        for ent in raw.get("new_entities", []):
            new_entities.append({
                "name": ent.get("name", ""),
                "type": ent.get("type", "person"),
                "brief": ent.get("brief", ""),
            })

        # ── 倒排索引更新 ──
        index_updates = {}
        for name in raw.get("entities_present", []):
            if name and isinstance(name, str):
                idx = self.reader.read_entity_index() or {"entities": {}}
                existing = idx.get("entities", {}).get(name, {}).get("chapters", [])
                if chapter_number not in existing:
                    existing.append(chapter_number)
                index_updates[name] = existing

        return {
            "summary_meta": summary_meta,
            "summary_body": f"## 摘要\n{raw.get('summary', '')}\n\n"
                           f"## 关键残留\n{raw.get('key_residue', '')}",
            "entity_updates": entity_updates,    # 旧格式（向后兼容）
            "entity_deltas": entity_deltas,       # 新格式（state.json）
            "new_entities": new_entities,
            "new_plots": raw.get("new_plots", []),
            "revealed_plots": raw.get("revealed_plots", []),
            "index_updates": index_updates,
        }

    def _extract_legacy_updates(self, entity_deltas: list[dict]) -> list[dict]:
        """从 entity_deltas 新格式中提取旧格式 entity_updates。"""
        updates = []
        for ent_delta in entity_deltas:
            entity_name = ent_delta.get("entity", "")
            for fact in ent_delta.get("facts", []):
                updates.append({
                    "entity": entity_name,
                    "field": fact.get("predicate", ""),
                    "new_value": fact.get("object", ""),
                    "old_value": fact.get("old_value", ""),
                })
        return updates

    def _convert_legacy_to_deltas(self, status_changes: list[dict], chapter_number: int) -> list[dict]:
        """将旧格式 status_changes 转换为新 entity_deltas 格式（无 evidence 的降级版本）。"""
        # 按实体名分组
        by_entity: dict[str, list[dict]] = {}
        for change in status_changes:
            name = change.get("entity", "")
            if name not in by_entity:
                by_entity[name] = []
            by_entity[name].append({
                "predicate": change.get("field", ""),
                "object": change.get("new_value", ""),
                "old_value": change.get("old_value", ""),
                "evidence": "",  # 旧格式没有 evidence
            })

        deltas = []
        for entity_name, facts in by_entity.items():
            # 尝试推断实体类型
            etype = "person"
            for t in ["person", "item", "location", "concept"]:
                if self.reader.read_entity(t, entity_name):
                    etype = t
                    break

            deltas.append({
                "entity": entity_name,
                "entity_type": etype,
                "facts": facts,
            })

        return deltas
