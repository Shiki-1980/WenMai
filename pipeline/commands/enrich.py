"""Auto-extracted from main.py."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from commands._utils import _clean_llm_output
from config_helper import CONFIG
from config_helper import get_paths as _get_paths
from generator import LLMGenerator
from reader import VaultReader
from state_schema import NovelSchema
from writer import VaultWriter


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
    print("提示：每写 10-20 章后跑一次 `python main.py enrich` 保持实体卡同步。")

    if getattr(args, 'review_schema', False):
        _review_schema(gen, reader, content_root, total_chapters)


def _review_schema(gen, reader, content_root, total_chapters):
    """用 LLM 审查 schema 的 enum 值，根据最新剧情建议扩充。"""
    schema = NovelSchema.load(content_root)
    if not schema:
        print("\n[schema-review] 当前无 schema，跳过审查。先运行 `python main.py init-schema`。")
        return

    # 收集近期章节摘要作为上下文
    recent_summaries = []
    start_ch = max(1, total_chapters - 10)
    for ch in range(start_ch, total_chapters + 1):
        s = reader.read_summary(ch)
        if s:
            _, sbody = s
            recent_summaries.append(f"第{ch}章: {sbody[:200]}")

    if not recent_summaries:
        return

    # 构建 prompt：列出当前所有 enum 谓词及其允许值
    enum_sections = []
    for etype, es in schema.entity_schemas.items():
        preds = es.predicates_sorted()
        enum_preds = [p for p in preds if p.type == "enum" and p.values]
        if not enum_preds:
            continue
        lines = [f"## {etype} ({es.label})"]
        for p in enum_preds:
            lines.append(f"- {p.name}: {p.values}")
        enum_sections.append("\n".join(lines))

    if not enum_sections:
        print("\n[schema-review] 当前 schema 中没有 enum 类型谓词，无需审查。")
        return

    schema_text = "\n\n".join(enum_sections)
    context_text = "\n".join(recent_summaries[:10])

    print("\n[schema-review] 正在用 LLM 审查 schema enum 值...")
    system = (
        "你是小说 schema 维护者。根据故事的最新发展，审查当前 schema 中每个 enum 谓词的允许值列表。\n\n"
        "规则：\n"
        "- 只添加明显需要的新值（如角色达到了新的修炼境界、出现了新的物品品阶）\n"
        "- 不要删除已有值\n"
        "- 不要添加模糊或推测性的值\n"
        "- 每个新值应该有明确的剧情依据\n\n"
        "输出格式（严格 JSON）：\n"
        "{\n"
        '  "changes": [\n'
        '    {"entity_type": "person", "predicate": "修为", "new_values": ["化神", "渡劫"]},\n'
        '    {"entity_type": "item", "predicate": "品阶", "new_values": ["仙阶"]}\n'
        "  ]\n"
        "}\n"
        "如果没有需要添加的值，返回 {\"changes\": []}。不要任何前言。"
    )

    user = (
        f"当前小说已写 {total_chapters} 章。\n\n"
        f"## 当前 schema enum 值\n{schema_text}\n\n"
        f"## 近期剧情\n{context_text}\n\n"
        f"请审查上述 enum 值是否需要扩充。"
    )

    response = gen.generate(system, user, temperature=0.3)
    if not response:
        print("  LLM 未返回有效结果")
        return

    try:
        import json
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("\n```", 1)[0] if "\n```" in cleaned else cleaned[:-3]
        data = json.loads(cleaned.strip())
        changes = data.get("changes", [])
    except Exception:
        print(f"  无法解析 LLM 输出: {response[:100]}...")
        return

    if not changes:
        print("  schema enum 值已是最新，无需更新。")
        return

    added = 0
    for change in changes:
        etype = change.get("entity_type", "")
        predicate = change.get("predicate", "")
        new_values = change.get("new_values", [])
        if etype not in schema.entity_schemas or not predicate or not new_values:
            continue
        pdef = schema.get_predicate_def(etype, predicate)
        if pdef is None or pdef.type != "enum":
            continue
        existing = set(pdef.values)
        actual_new = [v for v in new_values if v not in existing]
        if actual_new:
            pdef.values.extend(actual_new)
            added += len(actual_new)
            print(f"  + [{etype}] {predicate}: {actual_new}")

    if added > 0:
        schema.save(content_root)
        print(f"\n[schema-review] 完成！添加了 {added} 个新 enum 值到 novel_schema.json")
    else:
        print("  schema enum 值已是最新，无需更新。")


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


