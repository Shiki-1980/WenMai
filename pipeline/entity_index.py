"""实体索引 —— 三层检索系统的核心。

Layer 1: Trie 子串扫描（canonical + aliases + auto_aliases → 精确匹配）
Layer 2: jieba 分词倒排索引（实体卡全文分词 → 属性词匹配）
Layer 3: LLM 搜索关键词（enrich 时生成，存入 term_index → 语义等价匹配）

索引文件：
  index/entity_alias_index.json  — Trie 的持久化备份
  index/entity_term_index.json   — 分词倒排索引
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Trie（前缀树，子串扫描）───────────────────────────────────

class TrieNode:
    __slots__ = ("children", "entity_names")

    def __init__(self):
        self.children: dict[str, TrieNode] = {}
        self.entity_names: set[str] = set()  # 该节点对应的 canonical 实体名


class EntityTrie:
    """前缀树，存储 canonical + aliases → canonical 实体名的映射。

    对输入文本做一次 O(N * L) 扫描（N=文本长度，L=最长模式串），
    同时命中所有匹配的模式串。
    """

    def __init__(self):
        self.root = TrieNode()
        self._max_len = 0  # 最长模式串长度，优化扫描窗口

    def insert(self, pattern: str, canonical_name: str):
        """插入一个模式串，指向 canonical 实体名。"""
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
        """扫描文本，返回 [(命中词, canonical实体名), ...]。

        对文本中每个位置，向前匹配最多 _max_len 个字符，
        在 Trie 中查找最长匹配。
        """
        if not text:
            return []

        results: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()  # 去重

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
        """移除某个模式串到实体的映射（软删除：只从叶子节点移除）。"""
        node = self.root
        for ch in pattern:
            if ch not in node.children:
                return
            node = node.children[ch]
        node.entity_names.discard(canonical_name)

    def rebuild(self, entity_alias_index: dict[str, "EntityAliasEntry"], canonical: str):
        """从 alias_index 重建一个实体的所有模式串。"""
        entry = entity_alias_index.get(canonical)
        if not entry:
            return
        for pattern in entry.all_patterns():
            self.insert(pattern, canonical)

    def __len__(self) -> int:
        """返回模式串数量（粗略）。"""
        return self._count_patterns(self.root)

    def _count_patterns(self, node: TrieNode) -> int:
        count = len(node.entity_names)
        for child in node.children.values():
            count += self._count_patterns(child)
        return count


# ── 别名索引条目 ───────────────────────────────────────────────

@dataclass
class EntityAliasEntry:
    """一个实体的所有可匹配模式串。"""
    canonical: str
    aliases: list[str] = field(default_factory=list)        # 手动声明的别名
    auto_aliases: list[str] = field(default_factory=list)   # EntityLinker 自动发现的

    def all_patterns(self) -> list[str]:
        """返回所有应插入 Trie 的模式串。"""
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


# ── jieba 分词倒排索引 ──────────────────────────────────────────

# 中文停用词（最小集）
_STOP_WORDS: set[str] = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "所", "为", "所以", "因为", "但是", "然而", "虽然", "可以", "这个",
    "那个", "什么", "怎么", "如何", "吗", "吧", "呢", "啊", "哦", "嗯",
    "已经", "还", "又", "再", "才", "刚", "将", "把", "被", "让", "从",
    "以", "对", "向", "与", "及", "或", "而且", "如果", "则", "虽",
    "但", "却", "只", "只是", "只有", "除了", "之外", "之后", "之前",
    "其中", "其他", "等等", "等", "某", "某些", "任何", "所有", "整个",
    "第", "章", "章末", "状态", "当前",
}

# 纯标点/数字正则
_RE_PUNCT_ONLY = re.compile(r"^[\s\d\W_]+$")


def is_valid_term(word: str) -> bool:
    """判断一个分词结果是否应纳入倒排索引。"""
    w = word.strip()
    if len(w) < 2:
        return False
    if w in _STOP_WORDS:
        return False
    if _RE_PUNCT_ONLY.match(w):
        return False
    return True


class TermIndex:
    """jieba 分词倒排索引：{term: {实体名, ...}}。

    索引来源：
    - 实体卡 frontmatter 所有字段值
    - 实体卡 body 全文
    - enrich 时 LLM 生成的 search_keywords
    """

    def __init__(self):
        self._index: dict[str, set[str]] = defaultdict(set)

    def add_entity_terms(self, entity_name: str, text: str):
        """将一段文本分词后，关联到指定实体。"""
        words = self._segment(text)
        for w in words:
            if is_valid_term(w):
                self._index[w].add(entity_name)

    def add_keywords(self, entity_name: str, keywords: list[str]):
        """添加 LLM 生成的搜索关键词（不做分词，直接加入）。"""
        for kw in keywords:
            kw = kw.strip()
            if len(kw) >= 2:
                self._index[kw].add(entity_name)

    def lookup(self, term: str) -> list[str]:
        """查找一个词关联的实体列表。"""
        return list(self._index.get(term, set()))

    def lookup_many(self, terms: list[str]) -> dict[str, list[str]]:
        """批量查找。{term: [实体名, ...]}"""
        result = {}
        for t in terms:
            entities = self.lookup(t)
            if entities:
                result[t] = entities
        return result

    def remove_entity(self, entity_name: str):
        """从索引中移除一个实体（更新前调用）。"""
        for term, entities in self._index.items():
            entities.discard(entity_name)
        # 清理空集合
        empty = [t for t, e in self._index.items() if not e]
        for t in empty:
            del self._index[t]

    @staticmethod
    def _segment(text: str) -> list[str]:
        """中文分词。"""
        try:
            import jieba
            return list(jieba.cut(text))
        except ImportError:
            # 降级：简单字符 n-gram
            return _fallback_segment(text)

    def to_dict(self) -> dict:
        return {"terms": {k: sorted(v) for k, v in self._index.items()}}

    @classmethod
    def from_dict(cls, data: dict) -> "TermIndex":
        ti = cls()
        for term, entities in data.get("terms", {}).items():
            ti._index[term] = set(entities)
        return ti

    def __len__(self) -> int:
        return len(self._index)


def _fallback_segment(text: str) -> list[str]:
    """无 jieba 时的降级分词：2-4 字滑动窗口。"""
    words = []
    clean = re.sub(r"[^一-鿿A-Za-z0-9]", "", text)
    for size in [4, 3, 2]:
        for i in range(len(clean) - size + 1):
            words.append(clean[i:i + size])
    return words


# ── 实体索引（打包 Trie + TermIndex）────────────────────────────

class EntityIndex:
    """三层检索系统的索引管理器。

    维护两个索引文件：
      index/entity_alias_index.json  — 别名列表（Trie 重建用）
      index/entity_term_index.json   — 分词倒排（TermIndex 持久化）
    """

    def __init__(self, index_dir: Path):
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._trie: EntityTrie | None = None
        self._term_index: TermIndex | None = None
        self._alias_entries: dict[str, EntityAliasEntry] = {}

    # ── 加载 ──

    def load(self) -> "EntityIndex":
        """从磁盘加载所有索引。"""
        self._load_alias_index()
        self._load_term_index()
        self._build_trie()
        return self

    def _alias_index_path(self) -> Path:
        return self.index_dir / "entity_alias_index.json"

    def _term_index_path(self) -> Path:
        return self.index_dir / "entity_term_index.json"

    def _load_alias_index(self):
        path = self._alias_index_path()
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            self._alias_entries = {
                name: EntityAliasEntry.from_dict(entry)
                for name, entry in data.get("entities", {}).items()
            }

    def _load_term_index(self):
        path = self._term_index_path()
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            self._term_index = TermIndex.from_dict(data)
        else:
            self._term_index = TermIndex()

    def _build_trie(self):
        """从 alias_entries 重建 Trie。"""
        self._trie = EntityTrie()
        for entry in self._alias_entries.values():
            for pattern in entry.all_patterns():
                self._trie.insert(pattern, entry.canonical)

    # ── 查询 ──

    @property
    def trie(self) -> EntityTrie:
        if self._trie is None:
            self._build_trie()
        return self._trie

    @property
    def term_index(self) -> TermIndex:
        if self._term_index is None:
            self._term_index = TermIndex()
        return self._term_index

    def get_alias_entry(self, canonical: str) -> EntityAliasEntry | None:
        return self._alias_entries.get(canonical)

    def all_canonical_names(self) -> list[str]:
        return list(self._alias_entries.keys())

    # ── 更新 ──

    def add_entity(
        self,
        canonical: str,
        aliases: list[str] | None = None,
        auto_aliases: list[str] | None = None,
    ):
        """添加或更新一个实体的别名条目 + Trie。"""
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

        # 重建该实体的 Trie 模式串
        self.trie.rebuild(self._alias_entries, canonical)

    def index_entity_card(self, entity_name: str, card_text: str, keywords: list[str] | None = None):
        """将一张实体卡的全文加入词倒排索引。"""
        self.term_index.remove_entity(entity_name)  # 先清理旧索引
        self.term_index.add_entity_terms(entity_name, card_text)
        if keywords:
            self.term_index.add_keywords(entity_name, keywords)

    # ── 持久化 ──

    def save(self):
        """保存所有索引到磁盘。"""
        # 别名索引
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

        # 词倒排索引
        if self._term_index:
            term_data = {
                "version": 1,
                "last_updated": __import__('datetime').datetime.now().isoformat(),
                **self._term_index.to_dict(),
            }
            self._term_index_path().write_text(
                json.dumps(term_data, ensure_ascii=False, indent=2),
                "utf-8",
            )
