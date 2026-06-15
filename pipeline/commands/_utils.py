"""Auto-extracted from main.py."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from commit_store import CommitStore, DisambigRecord, build_commit_from_writer
from context_builder import ContextBuilder
from distiller import ChapterDistiller
from generator import LLMGenerator
from md_renderer import MarkdownRenderer
from reader import VaultReader
from retriever import EntityRetriever
from state_schema import (
    EntityFact,
    NovelSchema,
    load_entity_state,
    save_entity_state,
)
from writer import VaultWriter

logger = logging.getLogger(__name__)


# === lines 477-699 ===
def _clean_llm_output(text: str) -> str:
    """清洗 LLM 输出：去客套话、去代码块、去残留占位符。
    输出必须是干净的 Obsidian markdown（以 --- 或 # 开头）。"""
    import re
    text = text.strip()

    # 1. 去掉客套话首行（不以 # 或 --- 开头，且匹配客套模式）
    lines = text.split("\n")
    preface_patterns = [
        r"^(好的|[好很]的|[没无]问题|收到|明白|了解|我来|让我|以下|这是|为您)",
        r"^我将|^根据|^基于",
    ]
    if lines and not lines[0].startswith(("#", "---")):
        for pat in preface_patterns:
            if re.match(pat, lines[0].strip()):
                lines = lines[1:]
                break

    # 2. 去掉 markdown 代码块包裹（开头和结尾的 ``` 标记）
    if lines and re.match(r"^```(?:markdown|md|yaml|yml)?\s*$", lines[0].strip()):
        lines = lines[1:]  # 去掉开头的 ```markdown
    if lines and re.match(r"^```\s*$", lines[-1].strip()):
        lines = lines[:-1]  # 去掉结尾的 ```

    text = "\n".join(lines).strip()

    # 3. 兜底：正则去掉可能残留的代码块标记（跨行匹配）
    text = re.sub(r"^```(?:markdown|md|yaml|yml)?\s*\n", "", text, count=1)
    text = re.sub(r"\n```\s*$", "", text, count=1)

    # 4. 清理残留的 {{...}} 模板占位符
    text = re.sub(r"\{\{[^}]*\}\}", "", text)

    return text.strip()


def _create_stubs_for_missing_links(reader, writer, template_dir: Path):
    """扫描所有实体卡中的 [[wikilink]]，为缺失的实体创建占位 stub。"""
    from reader import parse_links as _pl

    # 收集所有现存实体名
    existing = set()
    for _etype, name in reader.all_entity_names():
        existing.add(name)

    # 扫描每张卡的 wikilink
    referenced: set[str] = set()
    for etype, name in reader.all_entity_names():
        card = reader.read_entity(etype, name)
        if card:
            meta, body = card
            referenced.update(_pl(body))
            for _key, value in meta.items():
                if isinstance(value, str):
                    referenced.update(_pl(value))
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            referenced.update(_pl(item))

    missing = referenced - existing
    if not missing:
        return

    print(f"\n发现 {len(missing)} 个被引用但缺失的实体，创建占位 stub...")
    for name in sorted(missing):
        if reader.find_entity_path(name):
            continue
        # 简单启发式推断类型（enrich 时会用 LLM 重新分类）
        etype = "person"
        from writer import _write_frontmatter_md
        subdir = writer.TYPE_DIR.get(etype, "person")
        card_path = writer.entity_dir / subdir / f"{name}.md"
        if card_path.exists():
            continue
        _write_frontmatter_md(
            card_path,
            {
                "type": etype,
                "status": "stub",
                "importance": "minor",
                "created": datetime.now().strftime("%Y-%m-%d"),
                "updated": datetime.now().strftime("%Y-%m-%d"),
            },
            f"# {name}\n\n## 描述\n（占位 — 由其他实体卡引用，待 `enrich` 命令补全。）\n",
        )
        print(f"  [stub] {name}")


def refresh_single_entity_markdown(reader, writer, state, etype, name, schema, renderer):
    """刷新单个实体的 markdown 文件：只更新 frontmatter，保留 body。"""
    from writer import _write_frontmatter_md

    card = reader.read_entity(etype, name)
    if not card:
        return
    original_meta, original_body = card

    # 从 state.json 生成新的 frontmatter
    importance = original_meta.get("importance", "major")
    new_fm = renderer.render_frontmatter(state, importance=importance)

    # 保留原始 frontmatter 中 state.json 没有的信息
    preserve_keys = {"aliases", "first_appearance", "last_appearance", "created", "type"}
    for k in preserve_keys:
        if k in original_meta and k not in new_fm:
            v = original_meta[k]
            if v:
                new_fm[k] = v

    new_fm["updated"] = __import__('datetime').datetime.now().strftime("%Y-%m-%d")

    subdir = writer.TYPE_DIR.get(etype, "person")
    card_path = writer.entity_dir / subdir / f"{name}.md"
    _write_frontmatter_md(card_path, new_fm, original_body)


def _post_write_commit(reader, writer, retriever, chapter_number, result, content_root, degraded: bool = False):
    """写后处理：保存 commit + 重新渲染受影响的实体 markdown。"""
    if degraded:
        print("  [STATE-DEGRADED] 跳过状态更新和 markdown 刷新")
        # 仍然保存 commit 用于审计（标记 degraded）
        try:
            store = CommitStore(Path(str(content_root)))
            from commit_store import ChapterCommit
            commit = ChapterCommit(
                chapter=chapter_number,
                retrieval_stats={"status": "state-degraded"},
            )
            store.save_commit(commit)
        except Exception:
            pass
        return

    try:
        # 加载 schema
        schema = NovelSchema.load(Path(str(content_root)))

        # 构建 commit
        disambigs = [
            DisambigRecord(
                candidate=d.get("candidate", ""),
                resolved_to=d.get("resolved_to"),
                confidence=d.get("confidence", 0.0),
                action=d.get("action", "auto"),
            )
            for d in result.get("disambiguations", [])
        ]

        commit = build_commit_from_writer(
            chapter=chapter_number,
            entity_deltas=result.get("entity_deltas", []),
            new_entities=[e.get("name", "") for e in result.get("new_entities", [])],
            disambiguations=disambigs,
            plots_added=result.get("new_plots", []),
            plots_resolved=result.get("revealed_plots", []),
            retrieval_stats=result.get("hit_details", {}),
        )
        store = CommitStore(Path(str(content_root)))
        store.save_commit(commit)

        # 重新渲染受影响的实体 markdown
        renderer = MarkdownRenderer(schema)
        affected = set()
        for delta in result.get("entity_deltas", []):
            affected.add(delta.get("entity", ""))
        for ent in result.get("new_entities", []):
            affected.add(ent.get("name", ""))

        for entity_name in affected:
            if not entity_name:
                continue
            # 找到实体类型
            etype = "person"
            for t in ["person", "item", "location", "concept"]:
                if reader.read_entity(t, entity_name):
                    etype = t
                    break

            state = reader.read_entity_state(etype, entity_name)
            if state:
                refresh_single_entity_markdown(
                    reader, writer, state, etype, entity_name, schema, renderer,
                )
                print(f"  -> markdown frontmatter 已刷新: {entity_name}")

        # 更新 entity_index
        if affected:
            try:
                retriever.rebuild_index()
            except Exception as e:
                logger.warning("重建 entity_index 失败: %s", e)

    except Exception as e:
        logger.warning("write commit 保存失败: %s", e)


def _auto_enrich(gen, reader, writer, content_root, template_dir):
    """轻量自动 enrich —— 每 10 章检查实体卡同步状态，不做全量 LLM 重写。"""
    total_chapters = reader.chapter_count()

    # 查找需要关注的实体
    needs_attention = []
    for etype, name in reader.all_entity_names():
        state = reader.read_entity_state(etype, name)
        appeared = reader.summaries_for_entity(name)
        if not appeared:
            continue
        max_ch = max(appeared)
        last_updated = state.last_updated_chapter if state else 0
        # 如果实体出现在最近的章节中，但状态未更新
        if max_ch > last_updated and max_ch >= total_chapters - 5:
            needs_attention.append((etype, name, last_updated, max_ch))

    if needs_attention:
        print(f"    发现 {len(needs_attention)} 个实体可能需要 enrich：")
        for etype, name, last, max_ch in needs_attention[:5]:
            print(f"      [{etype}] {name}: 最后更新 ch_{last}, 最新出现 ch_{max_ch}")
        if len(needs_attention) > 5:
            print(f"      ... 还有 {len(needs_attention) - 5} 个")
        print("    提示：运行 `python main.py enrich` 手动更新这些实体卡。")
    else:
        print("    所有实体卡已同步。")
# === lines 1356-1525 ===
def _write_one_chapter(
    reader: VaultReader,
    retriever: EntityRetriever,
    builder: ContextBuilder,
    gen: LLMGenerator,
    distiller: ChapterDistiller,
    writer: VaultWriter,
    arc_meta: dict,
    arc_body: str,
    chapter_number: int,
    chapter_outline: str,
    word_count: int,
    content_root: str = "",
    anti_ai: bool = False,
):
    """写一章的完整流程（Agent 循环 + 工具检索）。"""
    print(f"\n{'='*40}")
    print(f"  开始写作 第 {chapter_number} 章")
    print(f"{'='*40}")

    # 1. 组装最小上下文（大纲 + 基本参数）
    print("[1/3] 组装上下文...")
    context = builder.build_chapter_context(
        arc_meta, arc_body, chapter_number, chapter_outline, word_count
    )

    # 2. Agent 循环生成（LLM 通过工具按需检索）
    print("[2/3] Agent 循环写作...")
    from tools import ToolExecutor
    tool_exec = ToolExecutor(reader, {
        "chapter_number": chapter_number,
    })
    chapter_text = gen.generate_chapter_with_tools(context, tool_exec)
    if not chapter_text:
        logger.error("LLM 生成失败")
        return None

    # 提取标题
    lines = chapter_text.strip().split("\n")
    title_line = lines[0].strip("# ").strip() if lines else f"第{chapter_number}章"

    # 2.5 可选：去 AI 味改写
    if anti_ai:
        print("  [反AI] 正在去 AI 味改写 (L1规则→L2句法→L3润色→L4校验)...")
        known = set(name for _, name in reader.all_entity_names())
        polished, report = _run_anti_ai(gen, chapter_text, known)
        print(f"  [反AI] {report.summary()}")
        if not report.pass_all:
            logger.warning("去AI校验未通过: %d 段违规，已回退原文", report.l4_violations)
        chapter_text = polished
        # 更新标题（改写可能改变了首行）
        lines = chapter_text.strip().split("\n")
        title_line = lines[0].strip("# ").strip() if lines else title_line

    # 3. 蒸馏（Observer → Settler → Validator）
    print("[3/3] 蒸馏章节...")
    distill_result = distiller.distill(chapter_number, chapter_text)
    result = distill_result.data
    if distill_result.degraded:
        logger.warning("状态校验失败，正文保存但状态未更新 [STATE-DEGRADED]")
    if result:
        print(f"  -> 实体变化: {len(result.get('entity_updates', []))} 个")
        print(f"  -> 新实体: {len(result.get('new_entities', []))} 个")
        print(f"  -> 新伏笔: {result.get('new_plots', [])}")

    # 写回
    print("\n写回 vault...")
    writer.write_chapter(chapter_number, title_line, chapter_text)

    if result and not distill_result.degraded:
        writer.write_summary(
            chapter_number,
            result.get("summary_meta", {}),
            result.get("summary_body", ""),
        )

        for update in result.get("entity_updates", []):
            try:
                ent = retriever.resolve_entity(update["entity"])
                if ent:
                    writer.update_entity_field(
                        ent["type"], update["entity"],
                        update["field"], update["new_value"],
                        chapter_number,
                    )
            except Exception as e:
                logger.warning("更新实体 %s 失败: %s", update['entity'], e)

        # ── 新：应用 entity_deltas 到 state.json ──
        for ent_delta in result.get("entity_deltas", []):
            entity_name = ent_delta.get("entity", "")
            entity_type = ent_delta.get("entity_type", "person")
            facts = ent_delta.get("facts", [])
            if not entity_name or not facts:
                continue
            try:
                new_state = writer.apply_entity_delta(
                    entity_type, entity_name, chapter_number, facts,
                    reader=reader,
                )
                if new_state:
                    print(f"  -> state.json 已更新: {entity_name} (+{len(facts)} facts)")
            except Exception as e:
                logger.warning("state.json 更新 %s 失败 (cmd_write): %s", entity_name, e)

        for new_ent in result.get("new_entities", []):
            writer.create_entity(
                new_ent.get("type", "person"),
                new_ent["name"],
                new_ent.get("brief", ""),
            )
            # 新实体也初始化 state.json
            try:
                stub_state = EntityFact(
                    predicate="状态",
                    object="active",
                    since_chapter=chapter_number,
                    source=f"ch_{chapter_number:03d}创建",
                    evidence="新角色首次登场",
                )
                init_state = load_entity_state(
                    writer.entity_state_path(new_ent.get("type", "person"), new_ent["name"])
                )
                if init_state is None:
                    from state_schema import EntityState as ES
                    init_state = ES(
                        entity=new_ent["name"],
                        entity_type=new_ent.get("type", "person"),
                        last_updated_chapter=chapter_number,
                        facts=[stub_state],
                    )
                    save_entity_state(
                        init_state,
                        writer.entity_state_path(new_ent.get("type", "person"), new_ent["name"]),
                    )
            except Exception as e:
                logger.warning("新实体 state.json 初始化失败: %s", e)

            print(f"  -> 新实体: [{new_ent.get('type', 'person')}] {new_ent['name']}")

        for plot in result.get("new_plots", []):
            writer.add_plot_thread(plot, chapter_number)

        for plot in result.get("revealed_plots", []):
            writer.reveal_plot_thread(plot, chapter_number)
            print(f"  -> 伏笔回收: {plot}")

        writer.update_entity_index(result.get("index_updates", {}))

    # ── 保存 commit + 渲染 markdown ──
    _post_write_commit(
        reader, writer, retriever,
        chapter_number, result, content_root,
        degraded=distill_result.degraded,
    )

    print(f"\n第 {chapter_number} 章完成！")
    return chapter_text


# === lines 1941-1976 ===
def _progress_bar(done: int, total: int, width: int = 16) -> str:
    if total == 0:
        return ""
    filled = int(done / total * width)
    return "█" * filled + "░" * (width - filled)


def _parse_chapter_outlines(arc_body: str) -> dict[int, str]:
    """从篇章大纲正文中解析章节概要（支持表格和列表两种格式）。"""
    outlines = {}
    for line in arc_body.split("\n"):
        line = line.strip()

        # 表格格式：| 章节 | 概要 | ...
        if line.startswith("|") and not line.startswith("|---"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 2:
                try:
                    ch_num = int(cells[0])
                    outlines[ch_num] = cells[1]
                except ValueError:
                    pass
            continue

        # 列表格式：- **ch_01 标题：** 概要
        m = re.match(
            r"[-*]\s+\*{0,2}ch[._-]?\s*(\d+)[\s：:]*(.*)",
            line, re.IGNORECASE,
        )
        if m:
            ch_num = int(m.group(1))
            outline = re.sub(r"\*+", "", m.group(2)).strip("：: ").strip()
            if outline:
                outlines[ch_num] = outline[:120]
    return outlines


# Deprecated: 旧版 Anti-AI prompt（保留以兼容 polish.py --legacy 模式）
ANTI_AI_SYSTEM = (
    "你是资深网文编辑。将下面的章节改写为更自然、更有网文质感的中文文本。\n\n"
    "核心原则：\n"
    "- 只修改文笔和表达，不改变任何剧情、人物行为、对话内容、事件顺序\n"
    "- 所有人物名、地名、功法名、物品名等专有名词保持原样不动\n"
    "- [[wikilink]] 双链标楷和其中的实体名一字不改\n\n"
    "- 禁用的AI味表达（遇到则换掉）：\n"
    "  ・不仅…而且…、与此同时、总而言之、综上所述、诚然、不可否认\n"
    "  ・他/她深吸一口气、目光坚定、嘴角泛起/浮现/扬起、眼神复杂\n"
    "  ・仿佛、宛如、犹如（除非是恰当的比喻）\n"
    "  ・当然、毫无疑问、值得注意、重要的是、不可忽视\n\n"
    "- 句式要求：\n"
    "  ・长短句交替，对话穿插动作描写\n"
    "  ・用具体的感官细节代替抽象总结\n"
    "  ・把"他心里想"改为内心独白或行为暗示\n"
    "  ・减少"是/有/在"开头的判断句\n\n"
    "- 对话自然化：\n"
    "  ・去掉对话前的多余修饰（"他沉思片刻后说道"→直接引语）\n"
    "  ・对话节奏有快有慢，不要每句都带动作标签\n\n"
    "直接输出完整的改写后章节。不要任何前言、解释或标注。"
)


def _polish_chapter(gen, chapter_text: str) -> str:
    """[已废弃] 旧版去AI味改写。新代码请使用 _run_anti_ai()。"""
    return gen.generate(ANTI_AI_SYSTEM, chapter_text[:12000], temperature=0.7) or chapter_text


def _validate_polish(original: str, polished: str, known_entities: set[str]) -> list[str]:
    """[已废弃] 检查改写是否保留了关键实体。新代码请使用 anti_ai.layer4_validate。"""
    import re as _re
    orig_links = set(_re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]", original))
    new_links = set(_re.findall(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]", polished))
    missing = orig_links & known_entities - new_links
    return sorted(missing)


def _run_anti_ai(gen, chapter_text: str, known_entities: set[str] | None = None):
    """新版去AI味流水线：L1规则→L2句法→L3润色→L4校验。"""
    from anti_ai.pipeline import run_anti_ai_pipeline
    return run_anti_ai_pipeline(gen, chapter_text, known_entities)

