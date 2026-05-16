"""Context 组装器 —— 把检索结果拼成 LLM 可用的纯净上下文。"""

from state_schema import state_to_markdown_fragment, NovelSchema


def _format_entity_card(name: str, ent: dict, reader=None) -> str:
    """将一张实体卡格式化为 prompt 片段。
    优先使用 state.json 中的结构化状态，回退到 frontmatter。
    """
    meta = ent["metadata"]
    etype = meta.get("_type", "?")
    lines = [f"### [{etype}] {name}"]

    # 尝试从 state.json 获取权威状态
    state_text = ""
    if reader:
        state = reader.read_entity_state(etype, name)
        if state and state.facts:
            schema = getattr(reader, '_schema', None)
            state_text = state_to_markdown_fragment(state, schema)
            lines.append(state_text)

    # 回退到 frontmatter 状态
    if not state_text:
        if meta.get("status"):
            lines.append(f"- 状态：{meta['status']}")
        key_fields = ["修为", "身份", "所在", "持有", "当前状态", "能力", "效果", "category"]
        for k, v in meta.items():
            if k.startswith("_"):
                continue
            if k in key_fields or k in ("importance",):
                lines.append(f"- {k}：{v}")

    # concept 类型给足空间
    body_limit = 5000 if etype == "concept" else 3000
    lines.append(f"\n{ent['body'][:body_limit]}")
    return "\n".join(lines)


class ContextBuilder:
    def __init__(self, vault_reader):
        self.reader = vault_reader

    def build_chapter_context(
        self,
        arc_meta: dict,
        arc_body: str,
        chapter_number: int,
        chapter_outline: str,
        retrieved: dict,
        word_count: int = 3000,
    ) -> str:
        """组装写一章所需的完整上下文。"""

        # 篇章约束
        arc_constraints = arc_meta.get("constraints", "")

        # 实体卡（含 concept — 按需检索，不是全量 dump）
        entity_cards_text = ""
        entity_names = []
        for name, ent in retrieved.get("entity_cards", {}).items():
            entity_cards_text += _format_entity_card(name, ent, self.reader) + "\n\n"
            entity_names.append(name)

        # 顶层世界规则 —— 始终加载「世界观.md」
        world_bible = self.reader.read_world_bible()
        world_text = world_bible[1][:5000] if world_bible else ""

        # 兜底：按需检索的 concept 不足时，补 concept 摘要
        concept_in_context = {n for n, e in retrieved.get("entity_cards", {}).items()
                              if e.get("metadata", {}).get("_type") == "concept"}
        if len(concept_in_context) < 3:
            all_concepts = self.reader.world_constraints()
            if all_concepts:
                world_text += "\n\n" + all_concepts[:3000]

        # 最近摘要
        summaries = self.reader.recent_summaries(
            5, chapter_number
        )
        summaries_text = ""
        prev_residue = ""
        for ch_num, meta, body in summaries:
            summaries_text += f"## 第{ch_num}章摘要\n{body[:600]}\n\n"
            residue = meta.get("key_residue", "")
            if residue and ch_num == chapter_number - 1:
                prev_residue = residue

        # 伏笔
        active_plots = retrieved.get("active_plots", "")

        from prompts.generate_chapter import CHAPTER_USER

        return CHAPTER_USER.format(
            world_constraints=world_text,
            arc_title=arc_meta.get("title", ""),
            arc_summary=arc_body[:2000],
            arc_constraints=arc_constraints,
            chapter_outline=chapter_outline,
            active_plots=active_plots[:3000],
            entity_cards=entity_cards_text[:30000],
            recent_summaries=summaries_text[:8000],
            previous_residue=prev_residue,
            word_count=word_count,
            required_entities=", ".join(entity_names[:30]),
            chapter_number=chapter_number,
        )
