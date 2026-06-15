"""实体检索引擎 —— Trie 子串匹配 + wikilink 扩展。

Agent 循环模式下，检索主要被 lookup_entity 工具替代。
此模块保留用于：
  1. rebuild-index 命令（维护别名索引）
  2. entity_updates 处理（蒸馏后更新实体卡时需要 resolve_entity）
"""

from pathlib import Path

from entity_index import EntityIndex
from reader import VaultReader, parse_links


class EntityRetriever:
    def __init__(self, reader: VaultReader):
        self.reader = reader
        self._entity_cache: dict[str, dict] | None = None
        self._entity_index: EntityIndex | None = None

    @property
    def entity_index(self) -> EntityIndex:
        if self._entity_index is None:
            index_dir = Path(self.reader.root) / "index"
            self._entity_index = EntityIndex(index_dir).load()
        return self._entity_index

    def _load_cache(self):
        if self._entity_cache is None:
            self._entity_cache = self.reader.all_entities()

    def resolve_entity(self, name: str) -> dict | None:
        """获取实体完整信息。"""
        self._load_cache()
        ent = self._entity_cache.get(name)
        if ent is None:
            # 别名查找
            entry = self.entity_index.get_alias_entry(name)
            if entry:
                ent = self._entity_cache.get(entry.canonical)
        if ent is None:
            return None

        index = self.reader.read_entity_index()
        chapters = []
        if index:
            chapters = index.get("entities", {}).get(name, {}).get("chapters", [])

        return {
            "name": name,
            "type": ent["metadata"].get("_type", "unknown"),
            "metadata": ent["metadata"],
            "body": ent["body"],
            "linked_entities": ent["metadata"].get("_links", []),
            "chapters": chapters,
        }

    @staticmethod
    def _clean_name(name: str) -> str:
        links = parse_links(name)
        return links[0] if links else name

    def rebuild_index(self):
        """从所有实体卡重建 alias_index + Trie。"""
        self._entity_index = None
        idx = self.entity_index

        for etype, name in self.reader.all_entity_names():
            card = self.reader.read_entity(etype, name)
            if not card:
                continue
            meta, _ = card

            aliases = meta.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [a.strip() for a in aliases.split(",") if a.strip()]

            auto_aliases = meta.get("auto_aliases", [])
            if isinstance(auto_aliases, str):
                auto_aliases = [a.strip() for a in auto_aliases.split(",") if a.strip()]

            idx.add_entity(name, aliases=aliases, auto_aliases=auto_aliases)

        idx.save()
        print(f"  别名索引已重建: {len(idx.trie)} 个模式串, {len(idx.all_canonical_names())} 个实体")
