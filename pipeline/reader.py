"""Vault 读取层 —— 解析 Obsidian markdown + frontmatter + 双链 + JSON 状态文件。"""

import json
import re
from pathlib import Path

import frontmatter
from state_schema import (
    EntityFact,
    EntityState,
    load_entity_state,
)

LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]")


# 非实体命名空间前缀（伏笔等不应被当作实体提取）
_NON_ENTITY_PREFIXES = ("plot:",)


def parse_links(text: str) -> list[str]:
    """从文本中提取所有 [[双链]] 指向的实体名，过滤掉 plot: 等非实体命名空间。"""
    names = [m.strip() for m in LINK_RE.findall(text)]
    return [n for n in names if not any(n.startswith(p) for p in _NON_ENTITY_PREFIXES)]


def _combine_links(body: str, metadata: dict) -> list[str]:
    """从 frontmatter 的 list/dict 字段和正文中提取所有链接。"""
    links = parse_links(body)
    for _key, value in metadata.items():
        if isinstance(value, str):
            links.extend(parse_links(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    links.extend(parse_links(item))
                elif isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, str):
                            links.extend(parse_links(v))
    return sorted(set(links))


class VaultReader:
    def __init__(self, vault_path: str):
        self.root = Path(vault_path)
        self.entity_dir = self.root / "entity"
        self.person_dir = self.entity_dir / "person"
        self.item_dir = self.entity_dir / "item"
        self.location_dir = self.entity_dir / "location"
        self.concept_dir = self.entity_dir / "concept"
        self.chapter_dir = self.root / "chapter"
        self.summary_dir = self.root / "summary"
        self.plot_dir = self.root / "plot"
        self.index_dir = self.root / "index"

    # ---- 通用 markdown 读取 ----

    def _read_md(self, path: Path) -> tuple[dict, str] | None:
        """读取 markdown，返回 (metadata, body)。不存在返回 None。"""
        if not path.exists():
            return None
        post = frontmatter.loads(path.read_text("utf-8"))
        return dict(post.metadata), post.content

    def _list_md(self, directory: Path, pattern: str = "*.md") -> list[Path]:
        if not directory.exists():
            return []
        return sorted(directory.glob(pattern))

    # ---- 实体卡 ----

    def read_entity(self, entity_type: str, name: str) -> tuple[dict, str] | None:
        """读取指定实体卡。entity_type: person|item|location|concept。"""
        type_dir = {
            "person": self.person_dir,
            "item": self.item_dir,
            "location": self.location_dir,
            "concept": self.concept_dir,
        }
        d = type_dir.get(entity_type)
        if d is None:
            return None
        return self._read_md(d / f"{name}.md")

    def find_entity_path(self, name: str) -> Path | None:
        """根据名称在四个实体目录中查找实体卡。"""
        for d in [self.person_dir, self.item_dir, self.location_dir, self.concept_dir]:
            p = d / f"{name}.md"
            if p.exists():
                return p
        return None

    def all_entity_names(self) -> list[tuple[str, str]]:
        """返回所有实体 (type, name)。"""
        result = []
        for etype, d in [
            ("person", self.person_dir),
            ("item", self.item_dir),
            ("location", self.location_dir),
            ("concept", self.concept_dir),
        ]:
            for p in self._list_md(d):
                result.append((etype, p.stem))
        return result

    def all_entities(self) -> dict[str, dict]:
        """返回所有实体卡数据，key 为实体名。"""
        entities = {}
        for etype, name in self.all_entity_names():
            card = self.read_entity(etype, name)
            if card:
                meta, body = card
                meta["_type"] = etype
                meta["_links"] = _combine_links(body, meta)
                entities[name] = {"metadata": meta, "body": body}
        return entities

    # ---- 章节 ----

    def read_chapter(self, num: int) -> tuple[dict, str] | None:
        """读取章节正文。"""
        return self._read_md(self.chapter_dir / f"ch_{num:03d}.md")

    def chapter_count(self) -> int:
        return len(self._list_md(self.chapter_dir))

    # ---- 章节摘要 ----

    def read_summary(self, num: int) -> tuple[dict, str] | None:
        return self._read_md(self.summary_dir / f"ch_{num:03d}_summary.md")

    def recent_summaries(self, n: int, before_chapter: int) -> list[tuple[int, dict, str]]:
        """获取某章之前的最近 N 章摘要。"""
        results = []
        for ch_num in range(before_chapter - 1, 0, -1):
            s = self.read_summary(ch_num)
            if s is not None:
                results.append((ch_num, *s))
            if len(results) >= n:
                break
        results.reverse()
        return results

    def summaries_for_entity(self, entity_name: str) -> list[int]:
        """查倒排索引，返回某实体出现过的章节号。"""
        idx = self.read_entity_index()
        if idx is None:
            return []
        ent = idx.get("entities", {}).get(entity_name, {})
        return ent.get("chapters", [])

    # ---- 篇章大纲 ----

    def read_arc(self, arc_name: str) -> tuple[dict, str] | None:
        return self._read_md(self.plot_dir / "arcs" / f"{arc_name}.md")

    def list_arcs(self) -> list[str]:
        return [p.stem for p in self._list_md(self.plot_dir / "arcs")]

    # ---- 主线 & 伏笔池 ----

    def read_main_plot(self) -> tuple[dict, str] | None:
        return self._read_md(self.plot_dir / "主线.md")

    def read_world_bible(self) -> tuple[dict, str] | None:
        return self._read_md(self.plot_dir / "世界观.md")

    def read_plot_pool(self) -> tuple[dict, str] | None:
        return self._read_md(self.plot_dir / "伏笔池.md")

    # ---- 索引 ----

    def read_entity_index(self) -> dict | None:
        p = self.index_dir / "entity_chapter_index.json"
        if not p.exists():
            return None
        return json.loads(p.read_text("utf-8"))

    # ---- 世界观 ----

    def world_constraints(self) -> str:
        """收集所有 concept 实体的定义作为世界观约束。"""
        concepts = self._list_md(self.concept_dir)
        parts = []
        for p in concepts:
            card = self._read_md(p)
            if card:
                _, body = card
                parts.append(f"## {p.stem}\n{body}")
        return "\n\n".join(parts)

    # ---- JSON 状态文件 (.state.json) ----

    def state_dir(self) -> Path:
        return self.root / "state"

    def entity_state_path(self, entity_type: str, name: str) -> Path:
        """返回实体状态文件的路径。"""
        return self.state_dir() / entity_type / f"{name}.state.json"

    def read_entity_state(self, entity_type: str, name: str) -> EntityState | None:
        """读取实体的 JSON 状态文件（机器权威来源）。"""
        path = self.entity_state_path(entity_type, name)
        state = load_entity_state(path)
        if state is None:
            # 尝试从 markdown 迁移（frontmatter + body）
            card = self.read_entity(entity_type, name)
            if card:
                meta, body = card
                return self._migrate_from_markdown(entity_type, name, meta, body)
        return state

    def _migrate_from_markdown(self, entity_type: str, name: str, meta: dict, body: str = "") -> EntityState:
        """从 markdown frontmatter + body 迁移到 JSON 状态（一次性操作）。"""
        facts = []

        # 1. 从 frontmatter 的关键字段提取
        fm_fields = {
            "person": ["修为", "身份", "所在", "持有", "状态", "目标", "身体状态"],
            "item": ["current_holder", "location", "status", "category", "owner", "condition"],
            "location": ["parent_location", "掌控者/势力", "status"],
            "concept": ["category", "status", "scope"],
        }
        for field in fm_fields.get(entity_type, []):
            value = meta.get(field, "")
            if value and isinstance(value, str) and value.strip():
                facts.append(EntityFact(
                    predicate=field,
                    object=value.strip(),
                    since_chapter=0,
                    source="从 markdown frontmatter 迁移",
                ))

        # 2. 从 body 的「当前状态」/「基础信息」section 提取
        if body and not facts:
            facts = self._parse_body_state(entity_type, name, body)

        return EntityState(
            entity=name,
            entity_type=entity_type,
            last_updated_chapter=0,
            facts=facts,
        )

    def _parse_body_state(self, entity_type: str, name: str, body: str) -> list[EntityFact]:
        """从 markdown body 的列表项中解析状态事实。

        识别模式：
          - **字段名**：值
          - 字段名：值
        """
        import re
        facts = []
        # 提取「当前状态」section 的内容
        state_section = ""
        for section_pattern in [
            r"##\s*当前状态\s*\n(.*?)(?=\n##|\Z)",
            r"##\s*基础信息\s*\n(.*?)(?=\n##|\Z)",
        ]:
            m = re.search(section_pattern, body, re.DOTALL)
            if m:
                state_section += m.group(1) + "\n"

        if not state_section:
            return facts

        # 匹配列表项: "- key：value" 或 "- key: value" 或 "- **key**：value"
        field_labels = {
            "person": {
                "所在": "所在", "位置": "所在",
                "修为": "修为", "修为/实力": "修为", "实力": "修为",
                "持有": "持有",
                "身份": "身份",
                "状态": "状态", "身体/精神状态": "身体状态",
                "目标": "目标", "当前目标": "目标",
            },
            "item": {
                "current_holder": "current_holder", "持有者": "current_holder", "所在": "location",
                "status": "status", "状态": "status",
            },
            "location": {
                "parent_location": "parent_location", "所属": "parent_location",
                "掌控者/势力": "掌控者/势力", "掌控者": "掌控者/势力",
                "status": "status",
            },
        }

        labels = field_labels.get(entity_type, {})
        for line in state_section.split("\n"):
            line = line.strip()
            # 匹配: - **修为/实力**：真武境巅峰 或 - 修为：金丹
            m = re.match(r"[-*]\s*(?:\*\*)?([^：:*\n]+?)(?:\*\*)?\s*[：:]\s*(.+)", line)
            if m:
                raw_label = m.group(1).strip().rstrip("*").strip()
                value = m.group(2).strip().rstrip("*").strip()
                if not value or len(value) > 200:
                    continue
                # 去除 [[wikilink]] 和 markdown 粗体
                value = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", value)
                value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)

                # 匹配已知字段标签
                predicate = labels.get(raw_label)
                if not predicate:
                    # 尝试部分匹配
                    for label, pred in labels.items():
                        if label in raw_label or raw_label in label:
                            predicate = pred
                            break

                if predicate and value:
                    facts.append(EntityFact(
                        predicate=predicate,
                        object=value[:200],  # 限制长度
                        since_chapter=0,
                        source="从 markdown body 迁移",
                    ))

        return facts

    def all_entity_states(self) -> dict[str, EntityState]:
        """返回所有实体的 JSON 状态，key 为实体名。"""
        states = {}
        for etype, name in self.all_entity_names():
            state = self.read_entity_state(etype, name)
            if state:
                states[name] = state
        return states

    def entity_state_text(self) -> str:
        """所有实体状态的文本摘要（供 prompt 用）。"""
        lines = []
        for etype, name in self.all_entity_names():
            state = self.read_entity_state(etype, name)
            if state:
                active = state.get_all_active_facts()
                status = active.get("状态", active.get("status", "?"))
                lines.append(f"- [{etype}] {name}: {status}")
            else:
                lines.append(f"- [{etype}] {name}: ?")
        return "\n".join(lines)

    # ---- 实体摘要 ----

    def entity_state_summary(self) -> str:
        """所有实体当前状态的简要文本，供 outline generation 用。
        优先读取 JSON 状态文件，回退到 markdown frontmatter。"""
        lines = []
        for etype, name in self.all_entity_names():
            state = self.read_entity_state(etype, name)
            if state:
                active = state.get_all_active_facts()
                status = active.get("状态", active.get("status", "?"))
                lines.append(f"- [{etype}] {name}: {status}")
            else:
                card = self.read_entity(etype, name)
                if card:
                    meta, _ = card
                    lines.append(f"- [{etype}] {name}: {meta.get('status', '?')}")
        return "\n".join(lines)
