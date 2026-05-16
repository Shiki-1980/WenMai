"""实体驱动检索引擎 —— 三层检索：Trie精确名 + jieba分词倒排 + wikilink扩展。"""

from pathlib import Path

from reader import VaultReader, parse_links
from entity_index import EntityIndex, is_valid_term
from entity_linker import RetrievalDisambiguator, ChapterContext


class EntityRetriever:
    def __init__(self, reader: VaultReader):
        self.reader = reader
        self._entity_cache: dict[str, dict] | None = None
        self._entity_index: EntityIndex | None = None
        self._disambiguator = RetrievalDisambiguator()

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
        """获取实体完整信息：metadata, body, links, chapters。"""
        self._load_cache()
        ent = self._entity_cache.get(name)
        if ent is None:
            return None

        index = self.reader.read_entity_index()
        chapters = []
        if index:
            chapters = (
                index.get("entities", {}).get(name, {}).get("chapters", [])
            )

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
        """去除 [[wikilink]] 标记，返回纯实体名。"""
        links = parse_links(name)
        return links[0] if links else name

    def retrieve_for_chapter(
        self,
        chapter_outline: str,
        arc_entities: list[str],
        chapter_number: int = 0,
    ) -> dict:
        """
        三层检索：为写一章检索所有需要的上下文。

        Layer 1: Trie 子串扫描（canonical + aliases → 精确实体名）
        Layer 2: jieba 分词 + 倒排查（属性词 → 候选实体）
        Layer 3: wikilink 扩展（一跳，优先考虑 trie/倒排已命中实体）

        返回:
          {
            "entity_cards": {name: card},
            "active_plots": str,
            "entity_index_entries": {name: chapter_list},
            "hit_details": {  # 检索调试信息
              "trie_hits": [...],
              "term_hits": {...},
              "wikilink_hits": [...],
              "disambiguated": {...},
            }
          }
        """
        # ── Layer 1: Trie 子串扫描 ──
        trie_hits = self.entity_index.trie.scan(chapter_outline)
        exact_names: set[str] = {canonical for _, canonical in trie_hits}

        # ── Layer 2: jieba 分词 + 倒排查 ──
        term_hits: dict[str, list[str]] = {}
        try:
            import jieba
            words = [w for w in jieba.cut(chapter_outline) if is_valid_term(w)]
        except ImportError:
            words = _simple_tokenize(chapter_outline)

        for w in words:
            entities = self.entity_index.term_index.lookup(w)
            if entities:
                term_hits[w] = entities

        # ── 消歧前准备 ChapterContext ──
        ctx = self._build_chapter_context(chapter_number, arc_entities)

        # ── 合并候选 + 消歧 ──
        all_candidates = set(exact_names)
        disambig_log: dict[str, list[str]] = {}

        for word, entities in term_hits.items():
            if len(entities) == 1:
                all_candidates.add(entities[0])
            else:
                resolved = self._disambiguator.resolve_with_context(
                    word, entities, ctx, all_candidates,
                )
                if resolved:
                    all_candidates.add(resolved[0])
                    if len(entities) > 1:
                        disambig_log[word] = entities

        # ── 兜底：arc_entities 中的 wikilink ──
        wikilink_names: set[str] = set()
        for ae in arc_entities:
            links = parse_links(ae)
            if links:
                wikilink_names.update(links)
            else:
                wikilink_names.add(ae)

        # 大纲中的 wikilink
        outline_links = set(parse_links(chapter_outline))
        wikilink_names.update(outline_links)
        all_candidates.update(wikilink_names)

        # 冷启动兜底
        if not all_candidates:
            for ae in arc_entities:
                all_candidates.add(self._clean_name(ae))

        # ── 一跳 wikilink 扩展 ──
        expanded: set[str] = set(all_candidates)
        for name in list(all_candidates):
            ent = self.resolve_entity(name)
            if ent:
                expanded.update(ent["linked_entities"])

        # ── 收集实体卡 ──
        entity_cards: dict[str, dict] = {}
        for name in expanded:
            ent = self.resolve_entity(name)
            if ent:
                entity_cards[name] = ent
            elif name:
                entity_cards[name] = {
                    "name": name,
                    "type": "未知",
                    "metadata": {"_type": "未知", "status": "未知"},
                    "body": f"# {name}\n\n（自动占位 — 该实体尚未创建卡片。）\n",
                    "linked_entities": [],
                    "chapters": [],
                }

        # ── 活动伏笔 ──
        plot = self.reader.read_plot_pool()
        active_plots = ""
        if plot:
            _, body = plot
            active_plots = body

        # ── 索引条目 ──
        index_entries = {}
        for name in expanded:
            chapters = self.reader.summaries_for_entity(name)
            if chapters:
                index_entries[name] = chapters

        return {
            "entity_cards": entity_cards,
            "active_plots": active_plots,
            "entity_index_entries": index_entries,
            "hit_details": {
                "trie_hits": [(word, name) for word, name in trie_hits],
                "term_hits": term_hits,
                "wikilink_hits": list(wikilink_names),
                "disambiguated": disambig_log,
            },
        }

    def _build_chapter_context(self, chapter_number: int, arc_entities: list[str]) -> ChapterContext:
        """构建消歧用的章节上下文。"""
        # 主角所在位置
        protag_loc = ""
        entity_first: dict[str, int] = {}
        entity_last: dict[str, int] = {}
        entity_links: dict[str, list[str]] = {}

        # 从 state.json 获取主角位置
        all_states = self.reader.all_entity_states()
        for name, state in all_states.items():
            active = state.get_all_active_facts()
            # 主角
            if "protagonist" in str(active.get("importance", "")) or name in self._get_protagonist_names():
                protag_loc = active.get("所在", "")
            # 首末章节
            appeared = self.reader.summaries_for_entity(name)
            if appeared:
                entity_first[name] = min(appeared)
                entity_last[name] = max(appeared)

        # wikilink 链接
        self._load_cache()
        if self._entity_cache:
            for name, ent in self._entity_cache.items():
                entity_links[name] = ent["metadata"].get("_links", [])

        arc_clean = [self._clean_name(ae) for ae in arc_entities]

        return ChapterContext(
            current_chapter=chapter_number,
            protagonist_location=protag_loc,
            arc_entities=arc_clean,
            entity_first_chapter=entity_first,
            entity_last_chapter=entity_last,
            entity_links=entity_links,
        )

    def _get_protagonist_names(self) -> list[str]:
        """获取主角名（从 markdown importance 字段）。"""
        names = []
        for etype, name in self.reader.all_entity_names():
            card = self.reader.read_entity(etype, name)
            if card:
                meta, _ = card
                if meta.get("importance") == "protagonist":
                    names.append(name)
        return names

    def rebuild_index(self):
        """从所有实体卡重建 entity_index（enrich 后或首次初始化时调用）。"""
        self._entity_index = None
        idx = self.entity_index

        for etype, name in self.reader.all_entity_names():
            card = self.reader.read_entity(etype, name)
            if not card:
                continue
            meta, body = card

            # aliases
            aliases = meta.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [a.strip() for a in aliases.split(",") if a.strip()]

            # auto_aliases (from state.json auto_aliases)
            auto_aliases = meta.get("auto_aliases", [])
            if isinstance(auto_aliases, str):
                auto_aliases = [a.strip() for a in auto_aliases.split(",") if a.strip()]

            idx.add_entity(name, aliases=aliases, auto_aliases=auto_aliases)

            # 索引全文
            text = f"{' '.join(meta.get(k, '') for k in meta if isinstance(meta.get(k, ''), str))}\n{body}"
            idx.index_entity_card(name, text)

        idx.save()
        print(f"  实体索引已重建: {len(idx.trie)} 个模式串, {len(idx.term_index)} 个词条")

    # 保留旧 API 兼容
    def _extract_names_from_text(self, outline: str, arc_entities: list[str]) -> set[str]:
        """从 arc_entities 提取实体名（冷启动兜底）。"""
        names: set[str] = set()
        for ae in arc_entities:
            name = self._clean_name(ae)
            if name:
                names.add(name)
        return names


def _simple_tokenize(text: str) -> list[str]:
    """无 jieba 时的简单分词：2-4 字滑动窗口 + 过滤。"""
    import re
    words = []
    clean = re.sub(r"[^一-鿿A-Za-z0-9]", "", text)
    for size in [4, 3, 2]:
        for i in range(len(clean) - size + 1):
            w = clean[i:i + size]
            if is_valid_term(w):
                words.append(w)
    return list(set(words))
