"""独立 polish 命令 —— 对已有章节做去 AI 味改写（L1规则→L2句法→L3润色→L4校验）。"""
from __future__ import annotations

from commands._utils import _run_anti_ai, _polish_chapter, _validate_polish
from config_helper import CONFIG, get_paths as _get_paths
from generator import LLMGenerator
from reader import VaultReader
from writer import VaultWriter


def cmd_polish(args):
    """对已有章节做去 AI 味改写，保留所有实体和剧情不变。"""
    content_root, _, _ = _get_paths(getattr(args, 'novel', None))
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))
    writer = VaultWriter(str(content_root))

    ch = reader.read_chapter(args.chapter)
    if not ch:
        print(f"错误：找不到第 {args.chapter} 章")
        return

    meta, body = ch
    print(f"正在改写第 {args.chapter} 章...")

    known = set(name for _, name in reader.all_entity_names())

    if getattr(args, 'legacy', False):
        # 旧版模式（直接 LLM + wikilink 校验）
        print("  [legacy 模式]")
        polished = _polish_chapter(gen, body)
        missing = _validate_polish(body, polished, known)
        if missing and not args.force:
            print(f"警告：改写后丢失了以下实体的 [[wikilink]]: {missing}")
            print("使用 --force 跳过此检查，或手动检查后再试。")
            return
        if polished == body:
            print("改写结果与原文相同，跳过保存。")
            return
    else:
        # 新版分层流水线
        polished, report = _run_anti_ai(gen, body, known)
        print(f"  {report.summary()}")
        if not report.pass_all and not args.force:
            print(f"警告：L4 校验有 {report.l4_violations} 段违规（已自动回退原文）。")
            print("使用 --force 跳过此检查。")
            return
        if polished == body:
            print("改写结果与原文相同，跳过保存。")
            return

    # 保留原标题
    lines = polished.strip().split("\n")
    title_line = meta.get("title", lines[0].strip("# ").strip() if lines else f"第{args.chapter}章")

    writer.write_chapter(args.chapter, title_line, polished)
    print(f"第 {args.chapter} 章已改写并保存。")
