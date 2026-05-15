"""实体驱动检索引擎 —— 给定场景/实体，召回所有相关信息。"""

from reader import VaultReader, parse_links


class EntityRetriever:
    def __init__(self, reader: VaultReader):
        self.reader = reader
        self._entity_cache: dict[str, dict] | None = None  # name -> {metadata, body}

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

    def retrieve_for_chapter(self, chapter_outline: str, arc_entities: list[str]) -> dict:
        """
        为写一章检索所有需要的上下文。

        返回:
          {
            "entity_cards": {name: card},
            "active_plots": str,
            "entity_index_entries": {name: chapter_list}
          }
        """
        # 从大纲中提取 [[wikilink]] 实体
        mentioned: set[str] = set(parse_links(chapter_outline))

        # 从 arc_entities 中提取纯实体名（arc_entities 可能带有 [[wikilink]] 格式）
        for ae in arc_entities:
            links = parse_links(ae)
            if links:
                mentioned.update(links)
            else:
                mentioned.add(ae)

        # 也尝试直接从大纲文本中提取关键词（冷启动兜底：大纲可能不含 wikilink）
        if not mentioned:
            mentioned = self._extract_names_from_text(chapter_outline, arc_entities)

        # 解析每个实体，获取它们关联的其他实体（一跳扩展）
        expanded: set[str] = set(mentioned)
        for name in list(mentioned):
            ent = self.resolve_entity(name)
            if ent:
                expanded.update(ent["linked_entities"])

        # 收集实体卡（找不到卡的实体创建最小 stub）
        entity_cards: dict[str, dict] = {}
        for name in expanded:
            ent = self.resolve_entity(name)
            if ent:
                entity_cards[name] = ent
            elif name:
                # 冷启动：实体卡尚未创建，给一个最小 placeholder
                entity_cards[name] = {
                    "name": name,
                    "type": "未知",
                    "metadata": {"_type": "未知", "status": "未知（尚未建立实体卡）"},
                    "body": f"# {name}\n\n（自动占位 — 该实体尚未创建卡片，蒸馏后将自动建立。）\n",
                    "linked_entities": [],
                    "chapters": [],
                }

        # 活动伏笔
        plot = self.reader.read_plot_pool()
        active_plots = ""
        if plot:
            _, body = plot
            active_plots = body

        # 索引条目
        index_entries = {}
        for name in expanded:
            chapters = self.reader.summaries_for_entity(name)
            if chapters:
                index_entries[name] = chapters

        return {
            "entity_cards": entity_cards,
            "active_plots": active_plots,
            "entity_index_entries": index_entries,
        }

    def _extract_names_from_text(self, outline: str, arc_entities: list[str]) -> set[str]:
        """从 arc_entities 提取实体名（冷启动兜底：大纲不含 [[wikilink]] 时使用）。"""
        names: set[str] = set()
        for ae in arc_entities:
            name = self._clean_name(ae)
            if name:
                names.add(name)
        return names
