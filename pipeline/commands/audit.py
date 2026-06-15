"""Auto-extracted from main.py."""
from __future__ import annotations

from datetime import datetime

from commands._utils import _clean_llm_output
from config_helper import CONFIG
from config_helper import get_paths as _get_paths
from generator import LLMGenerator
from reader import VaultReader


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

        print("\n## 大纲")
        for arc_name in reader.list_arcs():
            arc = reader.read_arc(arc_name)
            if arc:
                meta, _ = arc
                print(f"  {arc_name}: {meta.get('chapter_range', '?')} [{meta.get('status', '?')}]")

        print("\n运行 `python main.py audit -r \"你的修改请求\"` 来修改。")
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
                print("    -> 已更新")

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
                print("    -> 已更新")

    print("\n审核修改完成。运行 `python main.py audit` 查看当前摘要。")

