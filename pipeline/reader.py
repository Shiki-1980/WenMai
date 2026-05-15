"""Vault 读取层 —— 解析 Obsidian markdown + frontmatter + 双链。"""

import json
import re
from pathlib import Path
from typing import Optional

import frontmatter
import yaml


LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]")


def parse_links(text: str) -> list[str]:
    """从文本中提取所有 [[双链]] 指向的实体名。"""
    return [m.strip() for m in LINK_RE.findall(text)]


def _combine_links(body: str, metadata: dict) -> list[str]:
    """从 frontmatter 的 list/dict 字段和正文中提取所有链接。"""
    links = parse_links(body)
    for key, value in metadata.items():
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
        post = frontmatter.load(str(path))
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

    # ---- 实体摘要 ----

    def entity_state_summary(self) -> str:
        """所有实体当前状态的简要文本，供 outline generation 用。"""
        lines = []
        for etype, name in self.all_entity_names():
            card = self.read_entity(etype, name)
            if card:
                meta, body = card
                lines.append(f"- [{etype}] {name}: {meta.get('status', '?')}")
        return "\n".join(lines)
