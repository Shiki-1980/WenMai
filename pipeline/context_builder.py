"""Context 组装器 —— 最小上下文，工具处理检索。

Agent 循环模式下，不再 pre-load 实体卡/世界观/摘要。
LLM 按需通过 lookup_entity / lookup_recent_events / check_world_rules 获取信息。
"""


class ContextBuilder:
    def __init__(self, vault_reader):
        self.reader = vault_reader

    def build_chapter_context(
        self,
        arc_meta: dict,
        arc_body: str,
        chapter_number: int,
        chapter_outline: str,
        word_count: int = 3000,
    ) -> str:
        """组装写一章所需的最小上下文。

        LLM 通过工具获取实体状态/世界观/剧情摘要，
        context 只提供写作指令和基本参数。
        """
        arc_constraints = arc_meta.get("constraints", "")

        # 上一章关键残留（这是唯一必须 pre-load 的——LLM 不知道"上一章写了什么"）
        prev_residue = ""
        summaries = self.reader.recent_summaries(1, chapter_number)
        if summaries:
            _, meta, _ = summaries[0]
            prev_residue = meta.get("key_residue", "")

        from prompts.generate_chapter import CHAPTER_USER

        return CHAPTER_USER.format(
            arc_title=arc_meta.get("title", ""),
            arc_summary=arc_body[:2000],
            arc_constraints=arc_constraints,
            chapter_outline=chapter_outline,
            previous_residue=prev_residue,
            word_count=word_count,
            chapter_number=chapter_number,
        )
