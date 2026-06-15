"""Auto-extracted from main.py."""
from __future__ import annotations

from pathlib import Path

from commands._utils import _progress_bar
from commit_store import CommitStore
from config_helper import get_paths as _get_paths
from reader import VaultReader
from state_schema import NovelSchema


def cmd_status(args):
    """显示当前写作状态。"""
    content_root, _, _ = _get_paths(getattr(args, 'novel', None))
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
    print("Index:")
    print(f"  alias_index: {'已存在' if alias_idx.exists() else '未生成'}")
    print(f"  term_index: {'已存在' if term_idx.exists() else '未生成'}")
    if not alias_idx.exists() and not term_idx.exists():
        print("  → 运行 `python main.py rebuild-index` 初始化索引")

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

    print("\n分卷进度:")
    arcs = []
    for arc_name in reader.list_arcs():
        arc = reader.read_arc(arc_name)
        if arc:
            arcs.append((arc_name, arc[0], arc[1]))

    # 按卷号分组
    from itertools import groupby
    arcs.sort(key=lambda x: x[1].get("volume", 0))
    for vol, group in groupby(arcs, key=lambda x: x[1].get("volume", 0)):
        group_list = list(group)
        vol_done = 0
        vol_total = 0
        lines = []
        for arc_name, meta, _ in group_list:
            cr = meta.get("chapter_range", "")
            title = meta.get("title", arc_name)
            done = 0
            total = 0
            if cr:
                parts = cr.split("-")
                if len(parts) == 2:
                    try:
                        arc_start, arc_end = int(parts[0]), int(parts[1])
                        total = arc_end - arc_start + 1
                        for ch in range(arc_start, arc_end + 1):
                            if reader.read_chapter(ch):
                                done += 1
                    except ValueError:
                        pass
            vol_done += done
            vol_total += total
            bar = _progress_bar(done, total) if total else ""
            lines.append(f"    {title}: {cr} | {done}/{total} {bar}")
        vol_bar = _progress_bar(vol_done, vol_total, 24) if vol_total else ""
        print(f"  第{vol}卷 | {vol_done}/{vol_total} {vol_bar}")
        for line in lines:
            print(line)
        print()

    if total_ch > 0 and reader.list_arcs():
        print("\n  → 续写: `python main.py write -a <arc名>`")

