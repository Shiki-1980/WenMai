"""Auto-extracted from main.py."""
from __future__ import annotations

import sys
from datetime import datetime

from commands._utils import _clean_llm_output
from config_helper import CONFIG
from config_helper import get_paths as _get_paths
from generator import LLMGenerator
from reader import VaultReader
from retriever import EntityRetriever


def cmd_worldbuild(args):
    """基于主线 + 已有实体卡，让 LLM 生成「世界观.md」。"""
    content_root, template_dir, vault_path = _get_paths(getattr(args, 'novel', None))
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
    bible_path_for_read = reader.plot_dir / "世界观.md"
    existing_body = ""
    try:
        existing = reader.read_world_bible()
        existing_body = existing[1] if existing else ""
    except Exception:
        if bible_path_for_read.exists():
            existing_body = bible_path_for_read.read_text("utf-8")
    existing_body = existing_body.replace("{{date}}", _today).replace("{{time}}", _now)

    # === 检索：解析主线中的 [[wikilink]]，拉取完整实体卡 ===
    from reader import _combine_links, parse_links
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

        import frontmatter as _fm
        from writer import _write_frontmatter_md
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
        print("  → 每章写作时自动加载（≤2500 字）")
    else:
        print("生成失败。")


