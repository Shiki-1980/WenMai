"""Auto-extracted from main.py."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from commands._utils import _clean_llm_output, _create_stubs_for_missing_links
from commands.enrich import _backfill_arc_entities
from config_helper import CONFIG, _auto_detect_volume
from config_helper import get_paths as _get_paths
from generator import LLMGenerator
from reader import VaultReader
from writer import VaultWriter


# === lines 109-475 ===
def _parse_chapter_range_from_outline(outline: str, fallback_start: int, num_chapters: int) -> tuple[int, int]:
    """从 LLM 输出中解析实际章节范围。LLM 自主决定章节数时使用。"""
    import re
    # 尝试从 frontmatter 或标题解析
    m = re.search(r"chapter_range:\s*\"?(\d+)\s*[-–—]\s*(\d+)", outline)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 回退
    if num_chapters <= 0:
        num_chapters = 30
    return fallback_start, fallback_start + num_chapters - 1


def _word_count_guidance(num_chapters: int) -> str:
    """生成弹性的每章字数指引。LLM 自行判断每章字数，高潮多写过渡少写。"""
    return (
        "字数根据剧情需要弹性调整，不要死板固定：\n"
        "  - 高潮/转折章节：4000-6000 字\n"
        "  - 推进/过渡章节：2500-3500 字\n"
        "  - 铺垫/日常章节：1500-2500 字\n"
        "LLM 自行判断每章的字数，不必每章统一。"
    )


def cmd_plan(args):
    """生成篇章大纲（卷规划）。"""
    content_root, template_dir, vault_path = _get_paths(getattr(args, 'novel', None))
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))
    writer = VaultWriter(str(content_root), str(template_dir))

    # 自动检测卷号
    volume = args.volume
    if volume == 0:
        volume = _auto_detect_volume(reader)

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

    # 章节数：用户指定 或 让 LLM 判断
    num_chapters = args.num_chapters if args.num_chapters > 0 else 0  # 0 = LLM 决定

    start_ch = current_ch + 1
    end_ch = start_ch + (num_chapters - 1) if num_chapters > 0 else start_ch + 29  # placeholder

    words_guidance = _word_count_guidance(num_chapters)

    prompt = OUTLINE_USER.format(
        main_plot=plot_body[:2000],
        world_setting=world_text[:3000],
        current_chapter=current_ch,
        recent_summaries=summary_text[:2000],
        entity_states=entity_states,
        active_plots=active_plots[:1000],
        user_direction=args.direction,
        num_chapters_arg=num_chapters,
        start_chapter=start_ch,
        words_per_chapter_guidance=words_guidance,
        volume_number=volume,
    )

    print(f"正在生成第 {volume} 卷大纲...")
    if num_chapters == 0:
        print("  (LLM 自行判断本卷章节数)")
    print()
    outline = gen.generate_outline(
        prompt,
        volume_number=volume,
        words_per_chapter_guidance=words_guidance,
        start_chapter=start_ch,
        end_chapter=end_ch,
    )

    # 从 LLM 输出解析实际章节范围
    actual_start, actual_end = _parse_chapter_range_from_outline(outline, start_ch, num_chapters)
    if num_chapters == 0:
        num_chapters = actual_end - actual_start + 1

    # 写入文件
    ch_start = actual_start
    ch_end = actual_end
    arc_name = args.name or f"v{volume:02d}_{ch_start:03d}_{ch_end:03d}"
    path = writer.root / "plot" / "arcs" / f"{arc_name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    # 如果 LLM 没输出 frontmatter，自动补上
    outline_stripped = outline.strip()
    if not outline_stripped.startswith("---"):
        outline = (
            "---\n"
            f"type: arc\n"
            f"status: planned\n"
            f"volume: {volume}\n"
            f"title: \"第{volume}卷\"\n"
            f"chapter_range: \"{ch_start}-{ch_end}\"\n"
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
        _generate_entity_cards(gen, reader, writer, outline, ch_start, ch_end, template_dir)
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
        "请输出 JSON 映射："
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

