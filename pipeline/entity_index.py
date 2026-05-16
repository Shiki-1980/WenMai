"""实体索引 —— 别名管理 + Trie 子串匹配。

Trie 用于从大纲文本中快速匹配实体名（canonical + aliases → 实体名）。
不包含分词倒排索引（Agent 循环中 LLM 通过 lookup_entity 工具按需检索）。

索引文件：
  index/entity_alias_index.json  — Trie 的持久化备份
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# ── Trie（前缀树，子串扫描）───────────────────────────────────

class TrieNode:
    __slots__ = ("children", "entity_names")

    def __init__(self):
        self.children: dict[str, TrieNode] = {}
        self.entity_names: set[str] = set()


class EntityTrie:
    """前缀树，存储 canonical + aliases → canonical 实体名的映射。

    对输入文本做一次 O(N * L) 扫描（N=文本长度，L=最长模式串），
    同时命中所有匹配的模式串。
    """

    def __init__(self):
        self.root = TrieNode()
        self._max_len = 0

    def insert(self, pattern: str, canonical_name: str):
        if not pattern or not pattern.strip():
            return
        node = self.root
        for ch in pattern:
            if ch not in node.children:
                node.children[ch] = TrieNode()
            node = node.children[ch]
        node.entity_names.add(canonical_name)
        self._max_len = max(self._max_len, len(pattern))

    def scan(self, text: str) -> list[tuple[str, str]]:
        """扫描文本，返回 [(命中词, canonical实体名), ...]"""
        if not text:
            return []

        results: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        for i in range(len(text)):
            node = self.root
            for j in range(i, min(i + self._max_len, len(text))):
                ch = text[j]
                if ch not in node.children:
                    break
                node = node.children[ch]
                if node.entity_names:
                    matched = text[i:j + 1]
                    for name in node.entity_names:
                        key = (matched, name)
                        if key not in seen:
                            results.append(key)
                            seen.add(key)

        return results

    def remove(self, pattern: str, canonical_name: str):
        node = self.root
        for ch in pattern:
            if ch not in node.children:
                return
            node = node.children[ch]
        node.entity_names.discard(canonical_name)

    def rebuild(self, entity_alias_index: dict[str, "EntityAliasEntry"], canonical: str):
        entry = entity_alias_index.get(canonical)
        if not entry:
            return
        for pattern in entry.all_patterns():
            self.insert(pattern, canonical)

    def __len__(self) -> int:
        return self._count_patterns(self.root)

    def _count_patterns(self, node: TrieNode) -> int:
        count = len(node.entity_names)
        for child in node.children.values():
            count += self._count_patterns(child)
        return count


# ── 别名索引条目 ───────────────────────────────────────────────

@dataclass
class EntityAliasEntry:
    canonical: str
    aliases: list[str] = field(default_factory=list)
    auto_aliases: list[str] = field(default_factory=list)

    def all_patterns(self) -> list[str]:
        patterns = [self.canonical]
        patterns.extend(a for a in self.aliases if a and a != self.canonical)
        patterns.extend(a for a in self.auto_aliases if a and a not in patterns)
        return patterns

    def to_dict(self) -> dict:
        return {
            "canonical": self.canonical,
            "aliases": self.aliases,
            "auto_aliases": self.auto_aliases,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EntityAliasEntry":
        return cls(
            canonical=data.get("canonical", ""),
            aliases=data.get("aliases", []),
            auto_aliases=data.get("auto_aliases", []),
        )


# ── 实体索引（Trie + Alias 管理）──────────────────────────────

class EntityIndex:
    """管理实体别名和 Trie 匹配。"""

    def __init__(self, index_dir: Path):
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._trie: EntityTrie | None = None
        self._alias_entries: dict[str, EntityAliasEntry] = {}

    def load(self) -> "EntityIndex":
        self._load_alias_index()
        self._build_trie()
        return self

    def _alias_index_path(self) -> Path:
        return self.index_dir / "entity_alias_index.json"

    def _load_alias_index(self):
        path = self._alias_index_path()
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            self._alias_entries = {
                name: EntityAliasEntry.from_dict(entry)
                for name, entry in data.get("entities", {}).items()
            }

    def _build_trie(self):
        self._trie = EntityTrie()
        for entry in self._alias_entries.values():
            for pattern in entry.all_patterns():
                self._trie.insert(pattern, entry.canonical)

    @property
    def trie(self) -> EntityTrie:
        if self._trie is None:
            self._build_trie()
        return self._trie

    def get_alias_entry(self, canonical: str) -> EntityAliasEntry | None:
        return self._alias_entries.get(canonical)

    def all_canonical_names(self) -> list[str]:
        return list(self._alias_entries.keys())

    def add_entity(self, canonical: str, aliases: list[str] | None = None,
                   auto_aliases: list[str] | None = None):
        entry = self._alias_entries.get(canonical)
        if entry is None:
            entry = EntityAliasEntry(canonical=canonical)
            self._alias_entries[canonical] = entry
        if aliases:
            for a in aliases:
                if a and a not in entry.aliases:
                    entry.aliases.append(a)
        if auto_aliases:
            for a in auto_aliases:
                if a and a not in entry.auto_aliases:
                    entry.auto_aliases.append(a)
        self.trie.rebuild(self._alias_entries, canonical)

    def save(self):
        alias_data = {
            "version": 1,
            "entities": {
                name: entry.to_dict()
                for name, entry in self._alias_entries.items()
            },
        }
        self._alias_index_path().write_text(
            json.dumps(alias_data, ensure_ascii=False, indent=2),
            "utf-8",
        )
