"""Novel Pipeline —— Obsidian + LLM 全自动网文写作系统。

Usage:
  python main.py plan --direction "..." --num-chapters 30
  python main.py write --arc <arc_name>
  python main.py write-one --chapter <N>
  python main.py distill --chapter <N>
  python main.py status
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml as _yaml_lib

from reader import VaultReader
from retriever import EntityRetriever
from context_builder import ContextBuilder
from generator import LLMGenerator
from distiller import ChapterDistiller
from writer import VaultWriter
from state_schema import (
    EntityFact, EntityState, StateDelta, NovelSchema,
    apply_delta_to_state, load_entity_state, save_entity_state,
)
from commit_store import CommitStore, build_commit_from_writer, DisambigRecord
from md_renderer import MarkdownRenderer

CONFIG = Path(__file__).parent / "config.yaml"


def _load_config():
    with open(CONFIG) as f:
        return _yaml_lib.safe_load(f)


def _get_paths(novel_override: str | None = None):
    """返回 (content_root, template_dir, vault_path)。确保小说目录结构存在。"""
    cfg = _load_config()
    vault_path = Path(cfg["vault"]["path"])
    novel_rel = novel_override or cfg["vault"].get("novel", "")
    if not novel_rel:
        print("错误：未指定小说。使用 --novel <名称> 或设置 config.yaml 中的 vault.novel")
        sys.exit(1)

    content_root = vault_path / novel_rel
    template_dir = vault_path / "_templates"

    # 自动创建小说目录结构
    for sub in [
        "entity/person", "entity/item", "entity/location", "entity/concept",
        "chapter", "summary", "plot/arcs", "index",
    ]:
        (content_root / sub).mkdir(parents=True, exist_ok=True)

    return content_root, template_dir, vault_path


def cmd_plan(args):
    """生成篇章大纲。"""
    content_root, template_dir, vault_path = _get_paths()
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))
    writer = VaultWriter(str(content_root), str(template_dir))

    # 收集上下文
    main_plot = reader.read_main_plot()
    plot_body = main_plot[1] if main_plot else ""

    current_ch = reader.chapter_count()
    summaries = reader.recent_summaries(5, current_ch + 1)
    summary_text = "\n".join(
        f"第{ch}章: {body[:200]}" for ch, _, body in summaries
    )

    entity_states = reader.entity_state_summary()
    active_plots = ""
    plot_pool = reader.read_plot_pool()
    if plot_pool:
        _, active_plots = plot_pool

    world_text = reader.world_constraints()

    from prompts.generate_outline import OUTLINE_USER

    start_ch = current_ch + 1
    end_ch = start_ch + args.num_chapters - 1

    prompt = OUTLINE_USER.format(
        main_plot=plot_body[:2000],
        world_setting=world_text[:3000],
        current_chapter=current_ch,
        recent_summaries=summary_text[:2000],
        entity_states=entity_states,
        active_plots=active_plots[:1000],
        user_direction=args.direction,
        num_chapters=args.num_chapters,
        start_chapter=start_ch,
        end_chapter=end_ch,
        words_per_chapter=3000,
    )

    print("正在生成篇章大纲...\n")
    outline = gen.generate_outline(prompt)

    # 写入文件
    arc_name = args.name or f"arc_{start_ch:03d}_{end_ch:03d}"
    path = writer.root / "plot" / "arcs" / f"{arc_name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    # 如果 LLM 没输出 frontmatter，自动补上
    outline_stripped = outline.strip()
    if not outline_stripped.startswith("---"):
        outline = (
            "---\n"
            f"type: arc\n"
            f"status: planned\n"
            f"chapter_range: \"{start_ch}-{end_ch}\"\n"
            f"title: \"\"\n"
            f"key_entities: []\n"
            f"constraints: \"\"\n"
            "---\n\n"
            + outline_stripped
        )

    path.write_text(outline, "utf-8")

    print(f"大纲已保存到: {path}")
    print("\n" + outline[:500] + "...")

    # 为 key_entities 中尚不存在的实体生成实体卡
    if args.entities:
        _generate_entity_cards(gen, reader, writer, outline, start_ch, end_ch, template_dir)
        # 扫描卡片间的 [[wikilink]] 引用，缺失的建占位 stub
        _create_stubs_for_missing_links(reader, writer, template_dir)
        # 把完整实体列表回写到 arc frontmatter 的 key_entities
        _backfill_arc_entities(reader, writer, path)


def _classify_entities_batch(gen, names: list[str], outline: str) -> dict[str, str]:
    """用 LLM 批量判断实体类型。返回 {name: type}。"""
    if not names:
        return {}

    system = (
        "你是小说实体分类助手。判断每个实体的类型。\n"
        "类型只能是: person, item, location, concept。\n"
        "只输出 JSON: {\"实体名\": \"类型\", ...}。不要任何前言、解释、markdown 代码块。\n\n"
        "分类标准（重要！）：\n"
        "- person: 有智慧、有意识的生命体。包括：人、妖、魔、兽、精灵、异族、器灵等。\n"
        "          关键判断：这个实体能自主思考和行动吗？能 → person。\n"
        "- item: 具体可持有/可使用的物件。包括：武器、法宝、丹药、功法秘籍、\n"
        "        材料、信物、矿石、符箓、容器等。\n"
        "        注意：功法秘籍（如《焚天诀》）是 item，但功法对应的「境界体系」是 concept。\n"
        "- location: 空间场所，有人物在其中活动。包括：城镇、宗门、学院、秘境、\n"
        "           山谷、房间、洞府、宫殿、集市、战场等。\n"
        "           注意：门派（如青云宗）、学院（如天武学院）有具体驻地 → location。\n"
        "                但门派所属的「组织势力」概念（如血手门的势力网络）→ concept。\n"
        "- concept: 抽象设定，非具体物件或场所。包括：修炼境界（如金丹期）、\n"
        "           能量体系（如灵气、真元）、制度规则（如禁武令）、\n"
        "           阵法原理、血脉天赋、世界观规则等。\n"
        "           口诀：看不见摸不着的抽象东西 → concept。\n"
    )
    user = (
        f"篇章大纲上下文：\n{outline[:2000]}\n\n"
        f"需要分类的实体：\n" + "\n".join(f"- {n}" for n in names) + "\n\n"
        f"请输出 JSON 映射："
    )

    raw = gen.generate(system, user, json_mode=True)
    try:
        import json
        # 提取 JSON 对象
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            return {k: v for k, v in json.loads(raw[start:end]).items() if v in ("person", "item", "location", "concept")}
    except Exception:
        pass
    return {}


def _extract_all_entities(gen, outline: str) -> list[dict]:
    """让 LLM 通读大纲，提取所有实体（含类型、重要程度、简述）。"""
    system = (
        "你是小说设定提取助手。通读篇章大纲，列出其中出现的每一个实体。\n"
        "不要遗漏任何角色、地点、物品、势力、功法、概念。\n"
        "只输出 JSON 数组，不要任何前言、客套话、解释文字。"
    )
    user = (
        f"{outline[:6000]}\n\n"
        f"提取以上大纲中出现的所有实体，输出 JSON 数组（不要 markdown 代码块）：\n"
        f'[{{"name": "实体名", "type": "person|item|location|concept", '
        f'"importance": "major|supporting|minor", '
        f'"brief": "一句话描述"}}, ...]\n\n'
        f"importance 判断标准：\n"
        f"  major   = 主角、主要反派、核心势力、贯穿多章的关键地点/物品\n"
        f"  supporting = 有台词/有戏份的配角，或只在本篇章内重要的实体\n"
        f"  minor   = 仅出现一章的路人、背景板地点、一次性提及的物品\n\n"
        f"分类标准：\n"
        f"person=有智慧的生命（人/妖/魔/兽）, item=具体物件（武器/法宝/丹药/秘籍）,\n"
        f"location=空间场所（城镇/宗门/学院/秘境）, concept=抽象设定（境界/能量/制度/规则）"
    )

    raw = gen.generate(system, user, json_mode=True)
    try:
        import json
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start != -1 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass
    return []


def _generate_entity_cards(gen, reader, writer, outline: str, start_ch: int, end_ch: int, template_dir: Path):
    """从大纲中提取所有实体，为尚不存在的生成卡片。"""
    import json as _json
    import frontmatter as _fm

    # 1. 解析 frontmatter 中的 key_entities
    try:
        post = _fm.loads(outline)
        meta = dict(post.metadata)
    except Exception:
        meta = {}

    key_entities = meta.get("key_entities", [])
    fm_names = set()
    for entry in key_entities:
        name = entry.replace("[[", "").replace("]]", "").strip() if "[[" in entry else entry.strip()
        fm_names.add(name)

    # 2. 让 LLM 从大纲正文中提取所有实体（含次要角色、地点、物品）
    print("\n正在从大纲中提取所有实体（含次要角色/地点/物品）...")
    all_extracted = _extract_all_entities(gen, outline)

    # 3. 合并：frontmatter key_entities + LLM 提取的实体
    extracted_map: dict[str, dict] = {}  # name -> {type, importance, brief}
    for ent in all_extracted:
        name = ent.get("name", "").strip()
        if name:
            extracted_map[name] = {
                "type": ent.get("type", "person"),
                "importance": ent.get("importance", "supporting"),
                "brief": ent.get("brief", ""),
            }

    # frontmatter 中的 key_entities 默认 major
    for name in fm_names:
        if name not in extracted_map:
            extracted_map[name] = {"type": "person", "importance": "major", "brief": ""}
        else:
            extracted_map[name]["importance"] = "major"

    if not extracted_map:
        print("(大纲中未找到任何实体，跳过实体卡生成)")
        return

    # 4. 筛掉已存在的
    new_entities = {n: d for n, d in extracted_map.items() if not reader.find_entity_path(n)}
    if not new_entities:
        print(f"(全部 {len(extracted_map)} 个实体已存在，跳过)")
        return

    print(f"  大纲中共 {len(extracted_map)} 个实体，其中 {len(new_entities)} 个需要新建")

    # 5. 对 LLM 未明确分类的实体，批量分类
    untyped = [n for n, d in new_entities.items() if d["type"] not in ("person", "item", "location", "concept")]
    if untyped:
        type_map = _classify_entities_batch(gen, untyped, outline)
        for n in untyped:
            new_entities[n]["type"] = type_map.get(n, "person")

    # 6. 读取各类型模板（替换 Obsidian 占位符）
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M")
    templates = {}
    for etype in ["person", "item", "location", "concept"]:
        tp = template_dir / f"{etype}.md"
        if tp.exists():
            t = tp.read_text("utf-8")
            t = t.replace("{{date}}", today).replace("{{time}}", now)
            templates[etype] = t

    # 7. 按重要程度分层生成
    #    minor  → 直接建 stub，不调 LLM
    #    supporting → 调 LLM 生成简化卡片
    #    major → 调 LLM 生成完整模板卡片
    from writer import _write_frontmatter_md

    majors = {n: d for n, d in new_entities.items() if d.get("importance") == "major"}
    supportings = {n: d for n, d in new_entities.items() if d.get("importance") == "supporting"}
    minors = {n: d for n, d in new_entities.items() if d.get("importance") == "minor"}

    if majors or supportings:
        print(f"\n生成 {len(majors)} 张完整卡 + {len(supportings)} 张简化卡"
              + (f"（{len(minors)} 个 minor 实体直接建 stub）" if minors else ""))

    for name, info in {**majors, **supportings}.items():
        etype = info["type"]
        is_major = info.get("importance") == "major"
        template_text = templates.get(etype, "").replace("{{title}}", name)

        if is_major:
            system_prompt = (
                f"你是小说设定设计师。为实体「{name}」撰写完整的设定卡片。\n\n"
                f"严格使用以下模板格式（不要省略任何章节）：\n{template_text}\n\n"
                f"规则：\n"
                f"- 模板中的占位符已填充完毕，直接在此基础上完善内容\n"
                f"- 每个章节都要有实质内容\n"
                f"- 信息基于大纲定位展开，不凭空编造\n"
                f"- 直接输出 markdown，不要任何前言或客套话"
            )
        else:
            system_prompt = (
                f"你是小说设定助手。为实体「{name}」创建简化设定卡。\n\n"
                f"模板参考：\n{template_text}\n\n"
                f"规则：\n"
                f"- 只填写「基础信息」「当前状态」「描述」三个章节\n"
                f"- 其他章节如果大纲中没有信息就留空或标注「待展开」\n"
                f"- 直接输出 markdown，不要任何前言或客套话"
            )

        user_prompt = (
            f"篇章大纲摘要：\n{outline[:2000]}\n\n"
            f"为「{name}」(类型: {etype}, 重要度: {'核心' if is_major else '配角'}) "
            f"生成设定卡片。"
        )

        print(f"  - {'[major]' if is_major else '[sup]'} {name} ({etype})...")
        card_text = gen.generate(system_prompt, user_prompt)
        if card_text:
            card_text = _clean_llm_output(card_text)
            try:
                card = _fm.loads(card_text)
                card_meta = dict(card.metadata)
                card_body = card.content
            except Exception:
                card_meta = {"type": etype, "status": "active"}
                card_body = card_text

            card_meta["importance"] = "major" if is_major else "supporting"
            card_meta["created"] = datetime.now().strftime("%Y-%m-%d")
            card_meta["updated"] = datetime.now().strftime("%Y-%m-%d")
            card_meta["enriched_through"] = 0

            subdir = writer.TYPE_DIR.get(etype, "person")
            card_path = writer.entity_dir / subdir / f"{name}.md"
            card_path.parent.mkdir(parents=True, exist_ok=True)
            _write_frontmatter_md(card_path, card_meta, card_body)
            print(f"    -> entity/{subdir}/{name}.md")

    # minor 实体直接建 stub
    for name, info in minors.items():
        etype = info["type"]
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
            f"# {name}\n\n## 描述\n{info.get('brief', '（占位 — 后续章节出现时会通过 enrich 补全。）')}\n",
        )
        print(f"  - [stub] {name} ({etype})")
        print(f"    -> entity/{subdir}/{name}.md")


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
    import frontmatter as _fm
    from reader import parse_links as _pl

    # 收集所有现存实体名
    existing = set()
    for etype, name in reader.all_entity_names():
        existing.add(name)

    # 扫描每张卡的 wikilink
    referenced: set[str] = set()
    for etype, name in reader.all_entity_names():
        card = reader.read_entity(etype, name)
        if card:
            meta, body = card
            referenced.update(_pl(body))
            for key, value in meta.items():
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
                print(f"  [WARN] 重建 entity_index 失败: {e}")

    except Exception as e:
        print(f"  [WARN] write commit 保存失败: {e}")


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
        print(f"    提示：运行 `python main.py enrich` 手动更新这些实体卡。")
    else:
        print(f"    所有实体卡已同步。")


def cmd_rebuild_index(args):
    """从实体卡重建 entity_index（Trie + 分词倒排）。"""
    content_root, template_dir, _ = _get_paths(getattr(args, 'novel', None))
    reader = VaultReader(str(content_root))
    retriever = EntityRetriever(reader)
    retriever.rebuild_index()
    print("完成。运行 `python main.py status` 查看状态。")


def cmd_init(args):
    """一键初始化新小说：目录→世界观→主线→实体→schema→大纲→索引。"""
    cfg = _load_config()
    vault_path = Path(cfg["vault"]["path"])
    template_dir = vault_path / "_templates"
    today = datetime.now().strftime("%Y-%m-%d")

    gen = LLMGenerator(str(CONFIG))

    # 自动生成书名
    if not args.name:
        print("正在根据描述生成书名...")
        name_raw = gen.generate(
            "你是小说命名助手。根据用户描述，生成一个简洁有力的小说书名（4-8字）。只输出书名，不要引号、不要解释。",
            f"题材: {args.genre}\n描述: {args.desc}",
            temperature=0.9,
        )
        novel_name = name_raw.strip().strip("《》\"'「」『』").split("\n")[0].strip()
        # 去掉文件系统不兼容字符
        novel_name = re.sub(r'[<>:"/\\|?*]', '', novel_name).strip()
        novel_name = novel_name or "未命名"
        print(f"  -> LLM 命名: {novel_name}")
    else:
        novel_name = args.name

    novel_path = vault_path / "novels" / novel_name

    print(f"\n{'='*50}")
    print(f"  初始化小说: {novel_name}")
    print(f"  题材: {args.genre}")
    print(f"  描述: {args.desc}")
    print(f"  路径: {novel_path}")
    print(f"{'='*50}\n")

    # ── Step 0: 创建目录结构 ──
    print("[0/6] 创建目录结构...")
    for sub in [
        "entity/person", "entity/item", "entity/location", "entity/concept",
        "chapter", "summary", "plot/arcs", "index", "state", "commits",
    ]:
        (novel_path / sub).mkdir(parents=True, exist_ok=True)

    reader = VaultReader(str(novel_path))
    writer = VaultWriter(str(novel_path), str(template_dir))

    # ── Step 1: 生成世界观 ──
    print("\n[1/6] 生成世界观...")
    world_path = novel_path / "plot" / "世界观.md"
    if world_path.exists() and not args.force:
        print("  -> 世界观已存在，跳过")
        world_body = world_path.read_text("utf-8")
    else:
        world_system = (
            f"你是资深{args.genre}题材世界观架构师。根据用户描述，构建一个自洽、有层次、有冲突空间的世界。\n\n"
            "必须包含：力量体系（完整等级列表）、地理（主要区域）、势力格局、世界规则。\n\n"
            "格式要求：所有实体（人名/地名/势力名/功法名/物品名/概念）必须用 [[wikilink]] 包裹。\n"
            "例如：[[青云宗]]位于[[中原九州]]北部，掌门[[玄真子]]修炼[[太虚剑诀]]已达[[真武境]]巅峰。\n"
            "直接输出完整的 markdown 文档（含 frontmatter），不要任何前言。"
        )
        world_raw = gen.generate(world_system, f"## 用户描述\n{args.desc}\n\n请生成完整的世界观设定。")
        if not world_raw:
            print("  [ERROR] 世界观生成失败"); return
        world_raw = _clean_llm_output(world_raw)
        world_path.write_text(world_raw, "utf-8")
        world_body = world_raw
        print("  -> 世界观已保存")

    # ── Step 2: 生成主线 ──
    print("\n[2/6] 生成主线...")
    main_path = novel_path / "plot" / "主线.md"
    if main_path.exists() and not args.force:
        print("  -> 主线已存在，跳过")
    else:
        from prompts.init_novel import INIT_MAIN_PLOT_SYSTEM, INIT_MAIN_PLOT_USER
        main_prompt = INIT_MAIN_PLOT_USER.format(
            novel_name=novel_name, genre=args.genre, description=args.desc,
            world_setting=world_body[:4000],
        )
        main_raw = gen.generate(
            INIT_MAIN_PLOT_SYSTEM.replace("{genre}", args.genre).replace("{date}", today),
            main_prompt,
        )
        if main_raw:
            main_raw = _clean_llm_output(main_raw)
            main_path.write_text(main_raw, "utf-8")
            print("  -> 主线已保存")

    reader = VaultReader(str(novel_path))

    # ── Step 3: 提取实体列表 + 生成 state.json + 渲染 markdown ──
    print("\n[3/6] 生成实体...")
    main = reader.read_main_plot()
    main_body = main[1] if main else ""
    entity_list_path = novel_path / "index" / "entity_list.json"

    # 3a. 提取实体列表 —— wikilink 解析 + 溯源 + 去重，全部先占位 stub
    if entity_list_path.exists() and not args.force:
        entities = json.loads(entity_list_path.read_text("utf-8"))
        print(f"  -> 实体列表已存在 ({len(entities)} 个)，跳过提取")
    else:
        from reader import parse_links

        # 从两个源文件解析 wikilink，记录出处
        def _extract_with_source(text: str, source_file: str) -> dict[str, dict]:
            """从文本中提取 wikilink，记录每个实体首次出现的上下文。"""
            result = {}
            lines = text.split("\n")
            for i, line in enumerate(lines):
                links = parse_links(line)
                for link in links:
                    if link not in result:
                        # 取前后各一行作为上下文
                        prev_line = lines[i-1].strip() if i > 0 else ""
                        next_line = lines[i+1].strip() if i+1 < len(lines) else ""
                        context = f"{prev_line} {line.strip()} {next_line}"[:200]
                        result[link] = {
                            "name": link,
                            "source_file": source_file,
                            "source_context": context.strip(),
                        }
            return result

        world_entities = _extract_with_source(world_body, "plot/世界观.md")
        main_entities = _extract_with_source(main_body, "plot/主线.md")

        # 合并去重（主线优先，因为主线信息更精确）
        all_raw = dict(world_entities)
        all_raw.update(main_entities)  # main overrides world on conflict

        print(f"  -> wikilink 解析：世界观 {len(world_entities)} + 主线 {len(main_entities)} → 去重 {len(all_raw)} 个")

        # LLM 分类 + 重要性判定（只做分类，不生成具体内容）
        if len(all_raw) <= 30:
            classify_raw = gen.generate(
                "你是小说实体分类助手。为每个实体判断 type 和 importance。\n"
                "type: person/item/location/concept\n"
                "importance: protagonist(主角,仅1个)/major(主线核心)/supporting(有戏份)/minor(仅提及)\n"
                "只输出 JSON 映射，不要任何前言。",
                f"## 主线摘要\n{main_body[:2000]}\n\n## 实体列表\n"
                + "\n".join(sorted(all_raw.keys()))
                + "\n\n输出 JSON: {{\"实体名\":{\"type\":\"...\",\"importance\":\"...\"}},...}",
                json_mode=True,
            )
            classifications = gen._parse_json(classify_raw) if classify_raw else {}
        else:
            classifications = {}

        # 组装最终实体列表
        entities = []
        for name, info in all_raw.items():
            cls = classifications.get(name, {})
            entities.append({
                "name": name,
                "type": cls.get("type", "person"),
                "importance": cls.get("importance", "supporting"),
                "aliases": [],
                "brief": "",
                "source_file": info["source_file"],
                "source_context": info["source_context"],
            })

        entity_list_path.write_text(json.dumps(entities, ensure_ascii=False, indent=2), "utf-8")
        print(f"  -> 实体列表已保存: {len(entities)} 个 (主角1, major {sum(1 for e in entities if e['importance']=='major')}, "
              f"supporting {sum(1 for e in entities if e['importance']=='supporting')}, minor {sum(1 for e in entities if e['importance']=='minor')})")

    # 3b. 生成 state.json —— 只有 protagonist 调 LLM，其余 stub
    print("  生成 state.json...")
    from state_schema import EntityState as ES
    retriever = EntityRetriever(reader)

    for ent in entities:
        name, etype, imp = ent["name"], ent.get("type", "person"), ent.get("importance", "major")
        state_path = writer.entity_state_path(etype, name)
        if state_path.exists() and not args.force:
            continue

        if imp == "protagonist":
            # 主角：LLM 生成完整 state
            state_raw = gen.generate(
                "你是小说设定助手。根据主线背景，为主角生成结构化 JSON 状态。\n"
                "输出严格 JSON：{\"facts\":[{\"predicate\":\"...\",\"object\":\"...\"},...]}\n"
                "人物谓词用：修为,身份,所在,持有,功法,天赋,技能,关系,目标,身体状态,详细描述\n"
                "只输出 JSON，不要任何前言。",
                f"主角: {name}\n主线背景: {main_body[:3000]}\n世界观: {world_body[:2000]}",
                json_mode=True,
            )
            facts = gen._parse_json(state_raw).get("facts", []) if state_raw else []
        else:
            # 其余实体：stub，记录出处，等待 enrich 补全
            facts = [
                {"predicate": "状态", "object": "stub"},
                {"predicate": "详细描述", "object": ent.get("brief", "")},
            ]
            # 记录来源信息
            facts.append({
                "predicate": "来源", "object": f"{ent.get('source_file','?')}: {ent.get('source_context','')[:200]}"
            })

        state = ES(entity=name, entity_type=etype, last_updated_chapter=0,
                    facts=[EntityFact(predicate=f["predicate"], object=f["object"],
                                      since_chapter=0, source="init") for f in facts])
        save_entity_state(state, state_path)

        # 别名索引
        retriever.entity_index.add_entity(name, aliases=ent.get("aliases", []))
        imp_label = {"protagonist": "主角", "major": "主要", "supporting": "配角", "minor": "次要"}.get(imp, "?")
        print(f"    [{imp_label}] {name}: {len(facts)} facts → state.json ({ent.get('source_file', '?')})")

    retriever.entity_index.save()

    # 3c. 从 state.json 渲染 markdown 实体卡
    print("  渲染 markdown 实体卡...")
    reader = VaultReader(str(novel_path))
    schema = NovelSchema.load(novel_path) or NovelSchema.default()
    renderer = MarkdownRenderer(schema)

    for ent in entities:
        name, etype, imp = ent["name"], ent.get("type", "person"), ent.get("importance", "major")
        state = load_entity_state(writer.entity_state_path(etype, name))
        if not state:
            continue
        card_path = writer.entity_dir / writer.TYPE_DIR.get(etype, "person") / f"{name}.md"
        if card_path.exists() and not args.force:
            continue

        body = renderer.render_entity_body(state, importance=imp)
        fm = renderer.render_frontmatter(state, importance=imp)
        fm["aliases"] = ent.get("aliases", [])
        fm["source_file"] = ent.get("source_file", "")
        fm["source_context"] = ent.get("source_context", "")[:200]
        fm["created"] = fm["updated"] = today
        from writer import _write_frontmatter_md
        _write_frontmatter_md(card_path, fm, body)

    reader = VaultReader(str(novel_path))

    # ── Step 4: 生成 schema ──
    print("\n[4/6] 生成 novel_schema.json...")
    from schema_gen import init_schema_for_novel
    init_schema_for_novel(gen, reader, novel_path, force=args.force)

    # ── Step 5: 生成第一卷大纲 ──
    print(f"\n[5/6] 生成第一卷大纲 ({args.chapters}章)...")
    arc_start, arc_end = 1, args.chapters
    arc_name = f"arc_{arc_start:03d}_{arc_end:03d}"
    arc_path = novel_path / "plot" / "arcs" / f"{arc_name}.md"
    if arc_path.exists() and not args.force:
        print(f"  -> {arc_name} 已存在，跳过")
    else:
        from prompts.generate_outline import OUTLINE_USER
        outline_raw = gen.generate_outline(OUTLINE_USER.format(
            main_plot=main_body[:2000], world_setting=world_body[:3000],
            current_chapter=0, recent_summaries="（新小说，无历史章节）",
            entity_states=reader.entity_state_summary(), active_plots="",
            user_direction=args.desc, num_chapters=args.chapters,
            start_chapter=arc_start, end_chapter=arc_end, words_per_chapter=4000,
        ))
        if outline_raw:
            outline_raw = outline_raw.strip()
            if not outline_raw.startswith("---"):
                all_names = sorted(name for _, name in reader.all_entity_names())
                key_ents = "\n".join(f"  - \"[[{n}]]\"" for n in all_names)
                outline_raw = f"---\ntype: arc\nstatus: planned\nchapter_range: \"{arc_start}-{arc_end}\"\ntitle: \"\"\nkey_entities:\n{key_ents}\nconstraints: \"\"\n---\n\n{outline_raw}"
            arc_path.parent.mkdir(parents=True, exist_ok=True)
            arc_path.write_text(outline_raw, "utf-8")
            print(f"  -> {arc_name} 已保存")

    # ── Step 6: 重建索引 ──
    print("\n[6/6] 重建索引...")
    reader = VaultReader(str(novel_path))
    EntityRetriever(reader).rebuild_index()

    # ── 更新 config.yaml ──
    cfg["vault"]["novel"] = f"novels/{novel_name}"
    with open(CONFIG, "w") as f:
        _yaml_lib.dump(cfg, f, allow_unicode=True, default_flow_style=False)

    print(f"\n{'='*50}")
    print(f"  初始化完成！")
    print(f"{'='*50}")
    print(f"\n  小说: {novel_name}")
    print(f"  实体: {len(reader.all_entity_names())} 个")
    print(f"  大纲: {arc_name}")
    print(f"\n  下一步: python main.py write --arc {arc_name} -y")




def cmd_list(args):
    """列出所有小说。"""
    cfg = _load_config()
    vault_path = Path(cfg["vault"]["path"])
    novels_dir = vault_path / "novels"
    if not novels_dir.exists():
        print("（无小说）")
        return

    active = cfg["vault"].get("novel", "")
    for d in sorted(novels_dir.iterdir()):
        if d.is_dir():
            marker = " ← 当前" if f"novels/{d.name}" == active else ""
            # 统计章节数
            ch_count = len(list((d / "chapter").glob("ch_*.md")))
            entity_count = sum(1 for _ in (d / "entity").rglob("*.md"))
            print(f"  {d.name}  ({ch_count}章, {entity_count}实体){marker}")


def cmd_switch(args):
    """切换活跃小说。"""
    cfg = _load_config()
    vault_path = Path(cfg["vault"]["path"])
    novel_rel = f"novels/{args.name}"
    novel_path = vault_path / novel_rel
    if not novel_path.exists():
        print(f"错误：小说不存在: {novel_path}")
        novels_dir = vault_path / "novels"
        if novels_dir.exists():
            existing = [d.name for d in novels_dir.iterdir() if d.is_dir()]
            if existing:
                print(f"现有小说: {', '.join(existing)}")
        return

    cfg["vault"]["novel"] = novel_rel
    with open(CONFIG, "w") as f:
        _yaml_lib.dump(cfg, f, allow_unicode=True, default_flow_style=False)
    print(f"已切换到: {args.name}")


def cmd_rename(args):
    """重命名小说。默认重命名当前活跃小说，也可指定任意小说。"""
    cfg = _load_config()
    vault_path = Path(cfg["vault"]["path"])

    # 确定要重命名的小说
    if args.name:
        old_rel = f"novels/{args.name}"
    else:
        old_rel = cfg["vault"].get("novel", "")
        if not old_rel:
            print("错误：未指定小说名，且 config.yaml 中 vault.novel 未设置")
            print("用法: python main.py rename [小说名] --to 新名称")
            return

    old_path = vault_path / old_rel
    if not old_path.exists():
        print(f"错误：小说目录不存在: {old_path}")
        novels_dir = vault_path / "novels"
        if novels_dir.exists():
            existing = [d.name for d in novels_dir.iterdir() if d.is_dir()]
            if existing:
                print(f"现有小说: {', '.join(existing)}")
        return

    new_rel = f"novels/{args.to}"
    new_path = vault_path / new_rel
    if new_path.exists():
        print(f"错误：目标已存在: {new_path}")
        return

    old_path.rename(new_path)
    print(f"已重命名: {old_rel} → {new_rel}")

    # 如果重命名的是活跃小说，更新 config.yaml
    if old_rel == cfg["vault"].get("novel", ""):
        cfg["vault"]["novel"] = new_rel
        with open(CONFIG, "w") as f:
            _yaml_lib.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        print(f"config.yaml 已更新")


def cmd_audit(args):
    """审核并修改已生成的内容。不传 --revise 则只展示摘要，传了则修改。"""
    content_root, template_dir, _ = _get_paths(getattr(args, 'novel', None))
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))

    if not args.revise:
        # 只展示摘要
        print("=" * 50)
        print("  当前项目摘要")
        print("=" * 50)

        world = reader.read_world_bible()
        print(f"\n## 世界观 ({'已生成' if world else '缺失'})")
        if world:
            print(world[1][:400] + "...")

        main = reader.read_main_plot()
        print(f"\n## 主线 ({'已生成' if main else '缺失'})")
        if main:
            print(main[1][:400] + "...")

        print(f"\n## 实体 ({len(reader.all_entity_names())} 个)")
        for etype, name in reader.all_entity_names():
            card = reader.read_entity(etype, name)
            imp = card[0].get("importance", "?") if card else "?"
            print(f"  [{etype}] {name} (importance={imp})")

        print(f"\n## 大纲")
        for arc_name in reader.list_arcs():
            arc = reader.read_arc(arc_name)
            if arc:
                meta, _ = arc
                print(f"  {arc_name}: {meta.get('chapter_range', '?')} [{meta.get('status', '?')}]")

        print(f"\n运行 `python main.py audit -r \"你的修改请求\"` 来修改。")
        return

    # ── 修改模式 ──
    target = args.target
    print(f"正在审核并修改: {target}")
    print(f"修改请求: {args.revise}\n")

    if target in ("world", "all"):
        world = reader.read_world_bible()
        if world:
            print("[世界] 修改世界观...")
            revised = gen.generate(
                "你是小说世界观架构师。根据修改请求，修订下面的世界观设定。只修改请求涉及的部分，其余保持不变。直接输出完整修订后的 markdown。",
                f"## 修改请求\n{args.revise}\n\n## 当前世界观\n{world}",
            )
            if revised:
                revised = _clean_llm_output(revised)
                (content_root / "plot" / "世界观.md").write_text(revised, "utf-8")
                print("  -> 世界观已更新")

    if target in ("plot", "all"):
        main = reader.read_main_plot()
        if main:
            print("[主线] 修改主线...")
            revised = gen.generate(
                "你是小说故事架构师。根据修改请求修订下面的主线。只修改请求涉及的部分。直接输出完整修订后的 markdown。",
                f"## 修改请求\n{args.revise}\n\n## 当前主线\n{main}",
            )
            if revised:
                revised = _clean_llm_output(revised)
                (content_root / "plot" / "主线.md").write_text(revised, "utf-8")
                print("  -> 主线已更新")

    if target in ("entities", "all"):
        print("[实体] 修改实体卡...")
        for etype, name in reader.all_entity_names():
            card = reader.read_entity(etype, name)
            if not card:
                continue
            meta, body = card
            imp = meta.get("importance", "minor")
            if imp not in ("protagonist", "major"):
                continue  # 只修改重要实体

            print(f"  - {name}...")
            revised = gen.generate(
                f"你是小说设定助手。根据修改请求修订实体「{name}」的设定卡。只修改请求涉及的部分。直接输出完整修订后的 markdown + frontmatter。",
                f"## 修改请求\n{args.revise}\n\n## 当前设定卡\n{body}",
            )
            if revised:
                revised = _clean_llm_output(revised)
                try:
                    card = __import__('frontmatter').loads(revised)
                    card_meta, card_body = dict(card.metadata), card.content
                except Exception:
                    card_meta = dict(meta)
                    card_body = revised
                card_meta["updated"] = datetime.now().strftime("%Y-%m-%d")
                from writer import _write_frontmatter_md
                subdir = {"person": "person", "item": "item", "location": "location", "concept": "concept"}.get(etype, "person")
                _write_frontmatter_md(content_root / "entity" / subdir / f"{name}.md", card_meta, card_body)
                print(f"    -> 已更新")

    if target in ("outline", "all"):
        for arc_name in reader.list_arcs():
            arc = reader.read_arc(arc_name)
            if not arc:
                continue
            meta, body = arc
            print(f"[大纲] 修改 {arc_name}...")
            revised = gen.generate(
                "你是小说大纲编辑。根据修改请求修订篇章大纲。只修改请求涉及的部分。直接输出完整修订后的 markdown + frontmatter。",
                f"## 修改请求\n{args.revise}\n\n## 当前大纲\n{body}",
            )
            if revised:
                revised = _clean_llm_output(revised)
                (content_root / "plot" / "arcs" / f"{arc_name}.md").write_text(revised, "utf-8")
                print(f"    -> 已更新")

    print(f"\n审核修改完成。运行 `python main.py audit` 查看当前摘要。")


def cmd_init_schema(args):
    """生成/更新 novel_schema.json。"""
    content_root, template_dir, _ = _get_paths(getattr(args, 'novel', None))
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))

    from schema_gen import init_schema_for_novel
    schema = init_schema_for_novel(gen, reader, content_root, force=args.force)
    if schema:
        print(f"\nSchema 已就绪: {content_root / 'novel_schema.json'}")
        print(f"运行 `python main.py status` 查看状态。")
    else:
        print("\nSchema 生成失败。检查 API 配置后重试。")


def cmd_enrich(args):
    """增量更新实体卡：对近期章节出现过的实体，用新剧情刷新卡片内容。"""
    content_root, template_dir, _ = _get_paths(getattr(args, 'novel', None))
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))
    writer = VaultWriter(str(content_root), str(template_dir))

    total_chapters = reader.chapter_count()
    if total_chapters == 0:
        print("还没有章节，无法 enrich。")
        return

    # 找出需要更新的实体：
    #   - stub 实体（总是需要）
    #   - 出现在章节中 且 enriched_through < 该章节号 的实体
    candidates: list[tuple[str, str, int]] = []  # (type, name, enriched_through)
    for etype, name in reader.all_entity_names():
        card = reader.read_entity(etype, name)
        if not card:
            continue
        meta, _ = card
        status = meta.get("status", "active")
        enriched = int(meta.get("enriched_through", 0))

        # stub 总是候选
        if status == "stub":
            candidates.append((etype, name, 0))
            continue

        # 查该实体在哪些章节出现
        appeared = reader.summaries_for_entity(name)
        if not appeared:
            continue
        max_ch = max(appeared)
        if max_ch > enriched:
            candidates.append((etype, name, enriched))

    if not candidates:
        print("所有实体卡已是最新。")
        return

    stub_count = sum(1 for _, _, e in candidates if e == 0)
    update_count = len(candidates) - stub_count
    print(f"需要更新 {len(candidates)} 个实体 (stub: {stub_count}, 增量: {update_count})")

    # 预读模板（替换 Obsidian 占位符）
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M")
    templates = {}
    for etype in ["person", "item", "location", "concept"]:
        tp = template_dir / f"{etype}.md"
        if tp.exists():
            t = tp.read_text("utf-8")
            t = t.replace("{{date}}", today).replace("{{time}}", now)
            templates[etype] = t

    import frontmatter as _fm

    updated = 0
    for etype, name, prev_enriched in candidates:
        # 收集该实体的相关章节摘要
        appeared = reader.summaries_for_entity(name)
        new_chapters = [ch for ch in appeared if ch > prev_enriched] if prev_enriched > 0 else appeared
        if not new_chapters and prev_enriched > 0:
            continue

        entity_text = ""
        if prev_enriched > 0:
            # 已有卡片存在，读取当前内容
            existing = reader.read_entity(etype, name)
            if existing:
                _, body = existing
                entity_text = f"\n当前实体卡内容：\n{body[:1500]}\n"

        # 收集相关章节摘要
        summary_parts = []
        for ch in new_chapters[-10:]:  # 最多取最近 10 章
            s = reader.read_summary(ch)
            if s:
                _, sbody = s
                summary_parts.append(f"## 第{ch}章\n{sbody[:400]}")
        chapter_context = "\n\n".join(summary_parts)

        is_stub = prev_enriched == 0
        action = "撰写" if is_stub else "更新"

        template_text = templates.get(etype, templates.get("person", ""))
        system = (
            f"你是小说设定图鉴的维护者。根据最新章节的剧情发展，{action}实体「{name}」的设定卡片。\n\n"
            + (f"严格使用以下模板格式：\n{template_text}\n\n"
               if is_stub else
               f"以下为当前卡片内容和模板格式（请在此基础上增量更新，保留已有正确信息）：\n\n"
               f"模板格式：\n{template_text}\n\n")
            + ("要求：每个章节都要有实质内容，不确定的标注「待展开」。\n"
               if is_stub else
               "原则：\n"
               "- 保留原有信息（除非与新剧情明确矛盾）\n"
               "- 新增的能力、关系、经历写入对应章节\n"
               "- 状态变化更新到「当前状态」章节\n"
               "- 不要编造未发生的剧情\n")
            + "直接输出 markdown，不要任何前言或客套话。"
        )

        user = (
            f"实体：{name} (类型: {etype})\n"
            f"{entity_text}"
            f"相关章节的新剧情：\n{chapter_context[:3000]}\n\n"
            f"请{action}该实体卡。"
        )

        print(f"  - {action} {name} ({etype})" + (f" [ch{min(new_chapters)}-{max(new_chapters)}]" if new_chapters else ""))
        card_text = gen.generate(system, user)
        if card_text:
            card_text = _clean_llm_output(card_text)
            try:
                card = _fm.loads(card_text)
                card_meta = dict(card.metadata)
                card_body = card.content
            except Exception:
                card_meta = {}
                card_body = card_text

            card_meta["status"] = "active"
            card_meta["enriched_through"] = max(new_chapters) if new_chapters else prev_enriched
            card_meta["updated"] = datetime.now().strftime("%Y-%m-%d")

            from writer import _write_frontmatter_md
            subdir = writer.TYPE_DIR.get(etype, "person")
            card_path = writer.entity_dir / subdir / f"{name}.md"
            _write_frontmatter_md(card_path, card_meta, card_body)
            print(f"    -> 已保存: entity/{subdir}/{name}.md (enriched_through={card_meta['enriched_through']})")
            updated += 1

    print(f"\n完成！更新了 {updated}/{len(candidates)} 个实体卡")
    print(f"提示：每写 10-20 章后跑一次 `python main.py enrich` 保持实体卡同步。")


def _backfill_arc_entities(reader, writer, arc_path: Path):
    """将所有已存在的实体名回写到 arc 的 key_entities。"""
    import frontmatter as _fm
    all_names = sorted(name for _, name in reader.all_entity_names())
    if not all_names:
        return
    try:
        post = _fm.loads(arc_path.read_text("utf-8"))
        meta = dict(post.metadata)
        body = post.content
    except Exception:
        return

    old_count = len(meta.get("key_entities", []))
    new_entities = [f"[[{n}]]" for n in all_names]
    if set(new_entities) == set(meta.get("key_entities", [])):
        return

    meta["key_entities"] = new_entities
    from writer import _write_frontmatter_md
    _write_frontmatter_md(arc_path, meta, body)
    print(f"  -> arc key_entities 已更新：{old_count} → {len(new_entities)} 个实体")


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
        print("  [ERROR] LLM 生成失败")
        return None

    # 提取标题
    lines = chapter_text.strip().split("\n")
    title_line = lines[0].strip("# ").strip() if lines else f"第{chapter_number}章"

    # 3. 蒸馏（Observer → Settler → Validator）
    print("[3/3] 蒸馏章节...")
    distill_result = distiller.distill(chapter_number, chapter_text)
    result = distill_result.data
    if distill_result.degraded:
        print(f"  [STATE-DEGRADED] 状态校验失败，正文保存但状态未更新")
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
                print(f"  [WARN] 更新实体 {update['entity']} 失败: {e}")

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
                print(f"  [WARN] state.json 更新 {entity_name} 失败: {e}")

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
                print(f"  [WARN] 新实体 state.json 初始化失败: {e}")

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


def cmd_worldbuild(args):
    """基于主线 + 已有实体卡，让 LLM 生成「世界观.md」。"""
    content_root, template_dir, vault_path = _get_paths()
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))
    retriever = EntityRetriever(reader)

    # 读取主线
    main = reader.read_main_plot()
    main_body = main[1] if main else ""
    if not main_body.strip():
        print("错误：请先创建 plot/主线.md")
        sys.exit(1)

    _today = datetime.now().strftime("%Y-%m-%d")
    _now = datetime.now().strftime("%H:%M")

    # 读取已有世界观（容错 YAML 解析失败：降级为纯文本读取）
    existing_body = ""
    try:
        existing = reader.read_world_bible()
        existing_body = existing[1] if existing else ""
    except Exception:
        if bible_path.exists():
            existing_body = bible_path.read_text("utf-8")
    existing_body = existing_body.replace("{{date}}", _today).replace("{{time}}", _now)

    # === 检索：解析主线中的 [[wikilink]]，拉取完整实体卡 ===
    from reader import parse_links, _combine_links
    main_meta = main[0] if main else {}
    mentioned_names = set(parse_links(main_body))
    mentioned_names.update(_combine_links(main_body, main_meta))

    # 按类型分组，展开 concept 卡全文
    concept_cards_text = ""
    other_cards_text = ""
    for name in mentioned_names:
        ent = retriever.resolve_entity(name)
        if not ent:
            continue
        etype = ent.get("type", "person")
        card_text = f"### [{etype}] {name}\n{ent['body'][:1500]}\n"
        if etype == "concept":
            concept_cards_text += card_text
        else:
            other_cards_text += card_text

    print(f"  从主线中解析到 {len(mentioned_names)} 个 wikilink，"
          f"其中 concept {concept_cards_text.count('### [concept]')} 个")

    # 读取模板
    bible_path = reader.plot_dir / "世界观.md"
    # 模板：优先用已有的世界观文件，不存在则用 _templates/世界观.md
    if bible_path.exists():
        template = bible_path.read_text("utf-8")
    else:
        tpl = template_dir / "世界观.md"
        template = tpl.read_text("utf-8") if tpl.exists() else ""
    template = template.replace("{{date}}", _today).replace("{{time}}", _now)

    action = "更新" if existing_body.strip() else "生成"

    system = (
        "你是资深世界观架构师。根据故事主线和已有的详细设定卡，构建一个自洽、有层次、有冲突空间的玄幻世界。\n\n"
        "原则：\n"
        "- 每个设定都要服务于故事冲突（力量体系制造障碍、势力格局制造对立、地理制造隔离）\n"
        "- 写确定的事实，不要模糊描述（'可能是...' → 删掉）\n"
        "- 引用已有 concept 卡的内容，用 [[wikilink]] 建立双向链接\n"
        "- 留白：标注「待探索」的区域，给后续剧情留想象空间\n"
        "- 直接输出完整的 markdown 文档（含 frontmatter），不要任何前言"
    )

    user = (
        f"## 故事主线\n{main_body[:3000]}\n\n"
        f"## 已检索到的 Concept 卡（世界观相关）\n{concept_cards_text[:5000]}\n\n"
        f"## 已检索到的其他实体卡（人物/地点/物品）\n{other_cards_text[:2000]}\n\n"
        + (f"## 当前世界观（请在此基础上更新）\n{existing_body[:3000]}\n\n" if existing_body else "")
        + ("## 模板（严格遵循此结构输出）\n" if not existing_body else "## 更新要求\n在保留原有正确设定的前提下，补充和完善各章节。\n")
        + (f"{template}\n\n" if not existing_body else "")
        + ("请生成完整的世界观设定文档。" if not existing_body else "请输出更新后的完整世界观文档。")
    )

    print(f"正在{action}世界观...")
    result = gen.generate(system, user)

    if result:
        result = _clean_llm_output(result)

        from writer import _write_frontmatter_md
        import frontmatter as _fm
        try:
            card = _fm.loads(result)
            meta = dict(card.metadata)
            body = card.content
        except Exception:
            meta = {"type": "world_bible", "status": "draft"}
            body = result

        meta["status"] = "active"
        meta["updated"] = datetime.now().strftime("%Y-%m-%d")
        _write_frontmatter_md(bible_path, meta, body)
        print(f"世界观已保存: {bible_path}")
        print(f"  → 每章写作时自动加载（≤2500 字）")
    else:
        print("生成失败。")


def cmd_write(args):
    """按篇章大纲逐章写作。"""
    content_root, template_dir, _ = _get_paths(getattr(args, 'novel', None))
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))
    retriever = EntityRetriever(reader)
    builder = ContextBuilder(reader)
    distiller = ChapterDistiller(gen, reader)
    writer = VaultWriter(str(content_root), str(template_dir))

    arc = reader.read_arc(args.arc)
    if not arc:
        print(f"错误：找不到篇章 '{args.arc}'")
        sys.exit(1)

    arc_meta, arc_body = arc
    chapter_range = arc_meta.get("chapter_range", "")
    if not chapter_range:
        print("错误：篇章缺少 chapter_range 字段")
        sys.exit(1)

    # 解析章节范围 "081-110"
    parts = chapter_range.split("-")
    start_ch = int(parts[0])
    end_ch = int(parts[1])

    # 解析章节大纲（从 arc body 的表格中提取）
    chapter_outlines = _parse_chapter_outlines(arc_body)

    # 统计已完成章节
    existing_chs = []
    for ch_num in range(start_ch, end_ch + 1):
        if reader.read_chapter(ch_num):
            existing_chs.append(ch_num)

    total = end_ch - start_ch + 1
    if existing_chs and not args.force:
        next_ch = max(existing_chs) + 1
        if next_ch > end_ch:
            print(f"此 arc ({start_ch}-{end_ch}) 全部 {total} 章已完成 ✅")
            return
        print(f"此 arc ({start_ch}-{end_ch})：已完成 {len(existing_chs)}/{total} 章，从第 {next_ch} 章继续")
        if args.force:
            print("  --force 已启用，将覆盖已有章节")
    else:
        print(f"此 arc ({start_ch}-{end_ch}) 共 {total} 章，冷启动开始写作")

    for ch_num in range(start_ch, end_ch + 1):
        outline = chapter_outlines.get(ch_num, f"第{ch_num}章，承接上文推进剧情")

        # 跳过已存在的
        if ch_num in existing_chs and not args.force:
            continue

        _write_one_chapter(
            reader, retriever, builder, gen, distiller, writer,
            arc_meta, arc_body, ch_num, outline,
            word_count=args.words,
        )

        # ── 自动 enrich：每写 10 章后自动刷新实体卡 ──
        if ch_num % 10 == 0 and ch_num > 0:
            print(f"\n  ⚡ 已写 10 章，自动运行 enrich...")
            _auto_enrich(gen, reader, writer, content_root, template_dir)

        if not args.yes:
            ans = input("\n继续写下一章？[Y/n/q] ")
            if ans.lower() == "q":
                print(f"\n已中断。当前进度：第 {ch_num}/{end_ch} 章。下次运行 `python main.py write -a {args.arc}` 继续。")
                break


def cmd_write_one(args):
    """写单独一章。"""
    content_root, template_dir, _ = _get_paths(getattr(args, 'novel', None))
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))
    retriever = EntityRetriever(reader)
    builder = ContextBuilder(reader)
    distiller = ChapterDistiller(gen, reader)
    writer = VaultWriter(str(content_root), str(template_dir))

    ch_num = args.chapter

    # 尝试找到所属篇章
    arc_meta = {}
    arc_body = ""
    for arc_name in reader.list_arcs():
        arc = reader.read_arc(arc_name)
        if arc:
            meta, body = arc
            cr = meta.get("chapter_range", "")
            if cr:
                parts = cr.split("-")
                if int(parts[0]) <= ch_num <= int(parts[1]):
                    arc_meta = meta
                    arc_body = body
                    break

    outline = args.outline or f"第{ch_num}章，推进剧情"

    _write_one_chapter(
        reader, retriever, builder, gen, distiller, writer,
        arc_meta, arc_body, ch_num, outline,
        word_count=args.words,
    )


def cmd_distill(args):
    """重新蒸馏指定章节（手动修改后使用）。"""
    content_root, template_dir, _ = _get_paths(getattr(args, 'novel', None))
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))
    distiller = ChapterDistiller(gen, reader)
    writer = VaultWriter(str(content_root), str(template_dir))

    ch = reader.read_chapter(args.chapter)
    if not ch:
        print(f"错误：找不到第 {args.chapter} 章")
        sys.exit(1)

    _, body = ch
    print(f"蒸馏第 {args.chapter} 章...")
    result = distiller.distill(args.chapter, body)

    if result:
        writer.write_summary(
            args.chapter,
            result.get("summary_meta", {}),
            result.get("summary_body", ""),
        )
        for update in result.get("entity_updates", []):
            ent = reader.find_entity_path(update["entity"])
            if ent:
                # 从路径推断类型
                parent = ent.parent.name
                writer.update_entity_field(
                    parent, update["entity"],
                    update["field"], update["new_value"],
                    args.chapter,  # 传入章节号，触发 state.json 同步
                )

        # ── 新：应用 entity_deltas 到 state.json ──
        for ent_delta in result.get("entity_deltas", []):
            entity_name = ent_delta.get("entity", "")
            entity_type = ent_delta.get("entity_type", "person")
            facts = ent_delta.get("facts", [])
            if not entity_name or not facts:
                continue
            try:
                writer.apply_entity_delta(
                    entity_type, entity_name, args.chapter, facts,
                    reader=reader,
                )
                print(f"  -> state.json 已更新: {entity_name}")
            except Exception as e:
                print(f"  [WARN] state.json 更新 {entity_name} 失败: {e}")

        for new_ent in result.get("new_entities", []):
            writer.create_entity(
                new_ent.get("type", "person"),
                new_ent["name"],
                new_ent.get("brief", ""),
            )
        for plot in result.get("new_plots", []):
            writer.add_plot_thread(plot, args.chapter)
        for plot in result.get("revealed_plots", []):
            writer.reveal_plot_thread(plot, args.chapter)
        writer.update_entity_index(result.get("index_updates", {}))
        print("蒸馏完成！")
    else:
        print("蒸馏失败，LLM 未返回有效结果")


def cmd_status(args):
    """显示当前写作状态。"""
    content_root, _, _ = _get_paths()
    reader = VaultReader(str(content_root))

    # Schema 状态
    schema = NovelSchema.load(Path(str(content_root)))
    if schema:
        print(f"Schema: v{schema.schema_version} ({schema.generated_at})")
        for etype in ["person", "item", "location", "concept"]:
            preds = schema.get_predicates(etype)
            print(f"  {etype}: {len(preds)} predicates {list(preds.keys())[:8]}...")
    else:
        print("Schema: 未生成 (运行 `python main.py init-schema` 生成)")

    # Index 状态
    idx_path = Path(str(content_root)) / "index"
    alias_idx = idx_path / "entity_alias_index.json"
    term_idx = idx_path / "entity_term_index.json"
    print(f"Index:")
    print(f"  alias_index: {'已存在' if alias_idx.exists() else '未生成'}")
    print(f"  term_index: {'已存在' if term_idx.exists() else '未生成'}")
    if not alias_idx.exists() and not term_idx.exists():
        print(f"  → 运行 `python main.py rebuild-index` 初始化索引")

    # Commit 状态
    store = CommitStore(Path(str(content_root)))
    commit_count = store.commit_count()
    print(f"Commits: {commit_count} 个章节提交")
    print()

    total_ch = reader.chapter_count()
    print(f"已写章节: {total_ch} 章")

    entities = reader.all_entity_names()
    stub_count = 0
    for etype, name in entities:
        card = reader.read_entity(etype, name)
        if card and card[0].get("status") == "stub":
            stub_count += 1
    print(f"实体总数: {len(entities)} (其中 stub: {stub_count})")
    for etype in ["person", "item", "location", "concept"]:
        count = sum(1 for t, _ in entities if t == etype)
        stubs = sum(1 for t, n in entities if t == etype and
                     (lambda c: c and c[0].get("status") == "stub")(reader.read_entity(t, n)))
        stub_str = f" (stub: {stubs})" if stubs else ""
        print(f"  {etype}: {count}{stub_str}")
    if stub_count:
        print(f"\n  → 运行 `python main.py enrich` 补全 {stub_count} 个 stub 实体")

    print(f"\n篇章大纲: {len(reader.list_arcs())} 个")
    for arc_name in reader.list_arcs():
        arc = reader.read_arc(arc_name)
        if arc:
            meta, _ = arc
            cr = meta.get("chapter_range", "")
            title = meta.get("title", arc_name)
            # 统计该 arc 已完成章节
            done = 0
            total_chapters = 0
            if cr:
                parts = cr.split("-")
                if len(parts) == 2:
                    try:
                        arc_start, arc_end = int(parts[0]), int(parts[1])
                        total_chapters = arc_end - arc_start + 1
                        for ch in range(arc_start, arc_end + 1):
                            if reader.read_chapter(ch):
                                done += 1
                    except ValueError:
                        pass
            bar = _progress_bar(done, total_chapters) if total_chapters else ""
            print(f"  {title}: {cr} | {done}/{total_chapters} {bar} [{meta.get('status', '?')}]")

    if total_ch > 0 and reader.list_arcs():
        print(f"\n  → 续写: `python main.py write -a <arc名>`")


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


def main():
    parser = argparse.ArgumentParser(description="Novel Pipeline")
    sub = parser.add_subparsers(dest="command")

    # 所有需要操作小说的命令共享 --novel
    def _add_novel_arg(p):
        p.add_argument("--novel", "-N", help="小说名称（默认: config.yaml 中的活跃小说）")

    p_plan = sub.add_parser("plan", help="生成篇章大纲")
    _add_novel_arg(p_plan)
    p_plan.add_argument("--direction", "-d", required=True, help="接下来想写的大方向")
    p_plan.add_argument("--num-chapters", "-n", type=int, default=30)
    p_plan.add_argument("--name", help="篇章名称（可选）")
    p_plan.add_argument("--entities", "-e", action="store_true", default=True,
                        help="同时生成实体卡（默认开启）")

    p_write = sub.add_parser("write", help="按篇章大纲逐章写作")
    _add_novel_arg(p_write)
    p_write.add_argument("--arc", "-a", required=True, help="篇章名称")
    p_write.add_argument("--words", "-w", type=int, default=3000)
    p_write.add_argument("--force", "-f", action="store_true", help="强制重写已存在章节")
    p_write.add_argument("--yes", "-y", action="store_true", help="自动连续写，不询问")

    p_one = sub.add_parser("write-one", help="写单独一章")
    _add_novel_arg(p_one)
    p_one.add_argument("--chapter", "-c", type=int, required=True)
    p_one.add_argument("--outline", "-o", help="本章概要（可选）")
    p_one.add_argument("--words", "-w", type=int, default=3000)

    p_distill = sub.add_parser("distill", help="重新蒸馏章节")
    _add_novel_arg(p_distill)
    p_distill.add_argument("--chapter", "-c", type=int, required=True)

    p_status = sub.add_parser("status", help="查看写作状态")
    _add_novel_arg(p_status)
    p_enrich = sub.add_parser("enrich", help="补全所有 stub 实体卡")
    _add_novel_arg(p_enrich)
    p_worldbuild = sub.add_parser("worldbuild", help="基于主线生成世界观设定")
    _add_novel_arg(p_worldbuild)

    p_init = sub.add_parser("init", help="一键初始化新小说项目")
    p_init.add_argument("name", nargs="?", help="小说名称（不填则 LLM 自动生成）")
    p_init.add_argument("--genre", "-g", default="xuanhuan", help="题材 (xuanhuan/xianxia/urban/scifi)")
    p_init.add_argument("--desc", "-d", required=True, help="一句话描述故事")
    p_init.add_argument("--chapters", "-n", type=int, default=30, help="第一卷章节数")
    p_init.add_argument("--force", "-f", action="store_true", help="覆盖已有文件")

    p_rename = sub.add_parser("rename", help="重命名小说")
    p_rename.add_argument("name", nargs="?", help="要重命名的小说（默认: 当前活跃小说）")
    p_rename.add_argument("--to", "-t", required=True, help="新名称")

    p_schema = sub.add_parser("init-schema", help="生成/更新 novel_schema.json")
    _add_novel_arg(p_schema)
    p_schema.add_argument("--force", "-f", action="store_true", help="强制重新生成")

    p_rebuild = sub.add_parser("rebuild-index", help="从实体卡重建 entity_index")
    _add_novel_arg(p_rebuild)

    sub.add_parser("list", help="列出所有小说")
    sub.add_parser("switch", help="切换活跃小说").add_argument("name", help="小说名称")

    p_audit = sub.add_parser("audit", help="审核并修改已生成的内容（世界观/主线/实体/大纲）")
    _add_novel_arg(p_audit)
    p_audit.add_argument("--revise", "-r", help="修改请求（如'主角性格太弱，加强'）")
    p_audit.add_argument("--target", "-t", default="all", help="修改目标: world/plot/entities/outline/all")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "plan":
        cmd_plan(args)
    elif args.command == "write":
        cmd_write(args)
    elif args.command == "write-one":
        cmd_write_one(args)
    elif args.command == "distill":
        cmd_distill(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "enrich":
        cmd_enrich(args)
    elif args.command == "worldbuild":
        cmd_worldbuild(args)
    elif args.command == "init-schema":
        cmd_init_schema(args)
    elif args.command == "rebuild-index":
        cmd_rebuild_index(args)
    elif args.command == "rename":
        cmd_rename(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "switch":
        cmd_switch(args)
    elif args.command == "audit":
        cmd_audit(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
