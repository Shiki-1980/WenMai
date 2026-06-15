"""Auto-extracted from main.py."""
from __future__ import annotations

import sys

from commands._utils import _auto_enrich, _parse_chapter_outlines, _write_one_chapter
from config_helper import CONFIG
from config_helper import get_paths as _get_paths
from context_builder import ContextBuilder
from distiller import ChapterDistiller
from generator import LLMGenerator
from reader import VaultReader
from retriever import EntityRetriever
from writer import VaultWriter


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
            content_root=str(content_root),
            anti_ai=getattr(args, 'anti_ai', False),
        )

        # ── 自动 enrich：每写 10 章后自动刷新实体卡 ──
        if ch_num % 10 == 0 and ch_num > 0:
            print("\n  ⚡ 已写 10 章，自动运行 enrich...")
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
        content_root=str(content_root),
        anti_ai=getattr(args, 'anti_ai', False),
    )


