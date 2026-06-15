"""Auto-extracted from main.py."""
from __future__ import annotations

from pathlib import Path

from commit_store import CommitStore
from config_helper import get_paths as _get_paths
from reader import VaultReader
from writer import VaultWriter


def cmd_rollback(args):
    """回滚指定章节的状态变更，恢复 entity state 到提交前的状态。"""
    content_root, _, _ = _get_paths(getattr(args, 'novel', None))
    store = CommitStore(Path(str(content_root)))
    reader = VaultReader(str(content_root))
    writer = VaultWriter(str(content_root))

    chapter = args.chapter
    commit = store.load_commit(chapter)
    if not commit:
        print(f"第 {chapter} 章没有提交记录，无法回滚。")
        print(f"可用提交: {store.list_commits()}")
        return

    if not args.force:
        print(f"\n即将回滚第 {chapter} 章的以下变更：")
        for ed in commit.entity_deltas_applied:
            print(f"  [{ed.entity_type}] {ed.entity}: "
                  f"{len(ed.facts_added)} 条事实变更, "
                  f"{len(ed.facts_retired)} 条事实退休")
        if commit.new_entities_created:
            print(f"  新建实体: {commit.new_entities_created}")
        if commit.plots_added:
            print(f"  新增伏笔: {len(commit.plots_added)} 条")
        if commit.plots_resolved:
            print(f"  回收伏笔: {len(commit.plots_resolved)} 条")
        ans = input("\n确认回滚？[y/N] ")
        if ans.lower() != "y":
            print("已取消。")
            return

    rolled = store.rollback_commit(chapter, reader, writer)
    print(f"\n回滚完成：恢复了 {rolled} 条事实变更。")
    print("注意：章节正文文件未被删除，如需重写请使用 write --force。")

