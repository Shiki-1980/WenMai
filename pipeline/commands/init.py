"""Auto-extracted from main.py."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from commands._utils import _clean_llm_output
from commands.plan import _word_count_guidance
from config_helper import CONFIG
from config_helper import load_config_rt as _load_config_rt
from config_helper import save_config as _save_config
from generator import LLMGenerator
from md_renderer import MarkdownRenderer
from reader import VaultReader
from retriever import EntityRetriever
from state_schema import EntityFact, NovelSchema, load_entity_state, save_entity_state
from writer import VaultWriter

logger = logging.getLogger(__name__)


def cmd_init(args):
    """一键初始化新小说：目录→世界观→主线→实体→schema→大纲→索引。"""
    cfg = _load_config_rt()
    vault_path = Path(cfg["vault"]["path"])
    template_dir = vault_path / "_templates"
    today = datetime.now().strftime("%Y-%m-%d")

    gen = LLMGenerator(str(CONFIG))

    # 自动生成书名
    if not args.name:
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
            logger.error("世界观生成失败")
            return
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
    arc_name = f"v01_{arc_start:03d}_{arc_end:03d}"
    arc_path = novel_path / "plot" / "arcs" / f"{arc_name}.md"
    if arc_path.exists() and not args.force:
        print(f"  -> {arc_name} 已存在，跳过")
    else:
        from prompts.generate_outline import OUTLINE_USER
        words_guidance = _word_count_guidance(args.chapters)
        outline_raw = gen.generate_outline(
            OUTLINE_USER.format(
                main_plot=main_body[:2000], world_setting=world_body[:3000],
                current_chapter=0, recent_summaries="（新小说，无历史章节）",
                entity_states=reader.entity_state_summary(), active_plots="",
                user_direction=args.desc, num_chapters_arg=args.chapters,
                start_chapter=arc_start, volume_number=1,
                words_per_chapter_guidance=words_guidance,
            ),
            volume_number=1,
            words_per_chapter_guidance=words_guidance,
            start_chapter=arc_start,
            end_chapter=arc_end,
        )
        if outline_raw:
            outline_raw = outline_raw.strip()
            if not outline_raw.startswith("---"):
                all_names = sorted(name for _, name in reader.all_entity_names())
                key_ents = "\n".join(f"  - \"[[{n}]]\"" for n in all_names)
                outline_raw = f"---\ntype: arc\nstatus: planned\nvolume: 1\nchapter_range: \"{arc_start}-{arc_end}\"\ntitle: \"\"\nkey_entities:\n{key_ents}\nconstraints: \"\"\n---\n\n{outline_raw}"
            arc_path.parent.mkdir(parents=True, exist_ok=True)
            arc_path.write_text(outline_raw, "utf-8")
            print(f"  -> {arc_name} 已保存")

    # ── Step 6: 重建索引 ──
    print("\n[6/6] 重建索引...")
    reader = VaultReader(str(novel_path))
    EntityRetriever(reader).rebuild_index()

    # ── 更新 config.yaml ──
    cfg["vault"]["novel"] = f"novels/{novel_name}"
    _save_config(cfg)

    print(f"\n{'='*50}")
    print("  初始化完成！")
    print(f"{'='*50}")
    print(f"\n  小说: {novel_name}")
    print(f"  实体: {len(reader.all_entity_names())} 个")
    print(f"  大纲: {arc_name}")
    print(f"\n  下一步: python main.py write --arc {arc_name} -y")



