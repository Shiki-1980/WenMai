"""蒸馏器 —— 从章节中提取结构化信息，更新实体状态和索引。"""

import json
from datetime import datetime


class ChapterDistiller:
    def __init__(self, generator, reader):
        self.generator = generator
        self.reader = reader

    def distill(self, chapter_number: int, chapter_text: str) -> dict:
        """
        蒸馏一章，返回结构化结果。
        {
          "summary_meta": dict,       # 章节摘要的 frontmatter
          "summary_body": str,        # 章节摘要正文
          "entity_updates": [...],    # 需要更新的实体卡变更列表
          "new_entities": [...],      # 需要创建的新实体
          "index_updates": {...},     # 倒排索引更新
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
        # 章节摘要的 frontmatter
        summary_meta = {
            "chapter": chapter_number,
            "entities_present": raw.get("entities_present", []),
            "status_changes": raw.get("status_changes", []),
            "new_entities": raw.get("new_entities", []),
            "revealed_plots": raw.get("revealed_plots", []),
            "new_plots": raw.get("new_plots", []),
            "keywords": raw.get("keywords", []),
            "key_residue": raw.get("key_residue", ""),
        }

        # 实体变更
        entity_updates = []
        for change in raw.get("status_changes", []):
            entity_updates.append({
                "entity": change.get("entity", ""),
                "field": change.get("field", ""),
                "new_value": change.get("new_value", ""),
                "old_value": change.get("old_value", ""),
            })

        # 新实体
        new_entities = []
        for ent in raw.get("new_entities", []):
            new_entities.append({
                "name": ent.get("name", ""),
                "type": ent.get("type", "person"),
                "brief": ent.get("brief", ""),
            })

        # 倒排索引更新
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
            "entity_updates": entity_updates,
            "new_entities": new_entities,
            "new_plots": raw.get("new_plots", []),
            "revealed_plots": raw.get("revealed_plots", []),
            "index_updates": index_updates,
        }
