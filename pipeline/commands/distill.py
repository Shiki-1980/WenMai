"""Auto-extracted from main.py."""
from __future__ import annotations

import logging
import sys

from config_helper import CONFIG
from config_helper import get_paths as _get_paths
from distiller import ChapterDistiller
from generator import LLMGenerator
from reader import VaultReader
from writer import VaultWriter

logger = logging.getLogger(__name__)


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
                logger.warning("state.json 更新 %s 失败 (cmd_distill): %s", entity_name, e)

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


