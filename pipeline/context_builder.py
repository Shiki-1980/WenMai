"""Context 组装器 —— 最小上下文，工具处理检索。

Agent 循环模式下，不再 pre-load 实体卡/世界观/摘要。
LLM 按需通过 lookup_entity / lookup_recent_events / check_world_rules 获取信息。
"""


class ContextBuilder:
    def __init__(self, vault_reader, schema=None):
        self.reader = vault_reader
        self.schema = schema

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

        # Schema 3.0: 注入主角性格锁定
        personality_lock = self._get_protagonist_personality_lock()
        ooc_constraints = self._get_active_ooc_constraints(chapter_number)

        # 伏笔上下文：从伏笔池读取活跃伏笔，注入写作提示
        plot_context = self._get_active_plots_context(chapter_number)

        from prompts.generate_chapter import CHAPTER_USER

        return CHAPTER_USER.format(
            arc_title=arc_meta.get("title", ""),
            arc_summary=arc_body[:2000],
            arc_constraints=arc_constraints,
            chapter_outline=chapter_outline,
            previous_residue=prev_residue,
            word_count=word_count,
            chapter_number=chapter_number,
            personality_lock=personality_lock,
            ooc_constraints=ooc_constraints,
            plot_context=plot_context,
        )

    def _get_protagonist_personality_lock(self) -> str:
        """从 schema 获取主角性格锁定。"""
        if not self.schema or not self.schema.protagonist_personality:
            return ""

        pp = self.schema.protagonist_personality
        lines = ["## 主角性格锁定（不可违反）"]
        for k, v in pp.items():
            lines.append(f"- {k}：{v}")
        return "\n".join(lines)

    def _get_active_ooc_constraints(self, chapter_number: int) -> str:
        """获取当前活跃实体的 OOC 约束（最近章节中出现的）。"""
        if not self.schema:
            return ""

        # 从最近章节摘要中获取活跃实体
        summaries = self.reader.recent_summaries(3, chapter_number)
        active_entities: set[str] = set()
        for _, meta, _ in summaries:
            for ent_list in meta.get("entities_present", []):
                if isinstance(ent_list, list):
                    active_entities.update(ent_list)
                elif isinstance(ent_list, str):
                    active_entities.add(ent_list)

        if not active_entities:
            return ""

        lines = ["## 实体 OOC 约束"]
        for name in list(active_entities)[:10]:
            # 查找实体类型
            for etype in ["person", "item", "location", "concept"]:
                card = self.reader.read_entity(etype, name)
                if card:
                    constraints = self.schema.get_ooc_constraints_for_entity(etype, name)
                    if constraints:
                        lines.append(f"### {name} [{etype}]")
                        lines.append(constraints)
                    break

        return "\n".join(lines) if len(lines) > 1 else ""

    def _get_active_plots_context(self, chapter_number: int) -> str:
        """从伏笔池读取活跃伏笔，生成注入写作提示的表格。"""
        from plot_health import parse_plot_pool_markdown, analyze_plot_health, build_plot_context

        md_path = self.reader.plot_dir / "伏笔池.md"
        if not md_path.exists():
            return ""

        threads = parse_plot_pool_markdown(md_path)
        if not threads:
            return ""

        health = analyze_plot_health(threads, chapter_number)
        return build_plot_context(health, max_plots=8)
