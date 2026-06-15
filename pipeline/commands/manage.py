"""Auto-extracted from main.py."""
from __future__ import annotations

from pathlib import Path

from config_helper import CONFIG
from config_helper import get_paths as _get_paths
from config_helper import load_config as _load_config
from config_helper import load_config_rt as _load_config_rt
from config_helper import save_config as _save_config
from generator import LLMGenerator
from reader import VaultReader
from retriever import EntityRetriever


def cmd_rebuild_index(args):
    """从实体卡重建 entity_index（Trie + 分词倒排）。"""
    content_root, template_dir, _ = _get_paths(getattr(args, 'novel', None))
    reader = VaultReader(str(content_root))
    retriever = EntityRetriever(reader)
    retriever.rebuild_index()
    print("完成。运行 `python main.py status` 查看状态。")


def cmd_init_schema(args):
    """生成/更新 novel_schema.json。"""
    content_root, template_dir, _ = _get_paths(getattr(args, 'novel', None))
    gen = LLMGenerator(str(CONFIG))
    reader = VaultReader(str(content_root))

    from schema_gen import init_schema_for_novel
    schema = init_schema_for_novel(gen, reader, content_root, force=args.force)
    if schema:
        print(f"\nSchema 已就绪: {content_root / 'novel_schema.json'}")
        print("运行 `python main.py status` 查看状态。")
    else:
        print("\nSchema 生成失败。检查 API 配置后重试。")



def cmd_list(args):
    """列出所有小说。"""
    cfg = _load_config()
    vault_path = Path(cfg["vault"]["path"])
    novels_dir = vault_path / "novels"
    if not novels_dir.exists():
        print("（无小说）")
        return

    active = cfg["vault"].get("novel", "")
    for d in sorted(novels_dir.iterdir()):
        if d.is_dir():
            marker = " ← 当前" if f"novels/{d.name}" == active else ""
            # 统计章节数
            ch_count = len(list((d / "chapter").glob("ch_*.md")))
            entity_count = sum(1 for _ in (d / "entity").rglob("*.md"))
            print(f"  {d.name}  ({ch_count}章, {entity_count}实体){marker}")


def cmd_switch(args):
    """切换活跃小说。"""
    cfg = _load_config_rt()
    vault_path = Path(cfg["vault"]["path"])
    novel_rel = f"novels/{args.name}"
    novel_path = vault_path / novel_rel
    if not novel_path.exists():
        print(f"错误：小说不存在: {novel_path}")
        novels_dir = vault_path / "novels"
        if novels_dir.exists():
            existing = [d.name for d in novels_dir.iterdir() if d.is_dir()]
            if existing:
                print(f"现有小说: {', '.join(existing)}")
        return

    cfg["vault"]["novel"] = novel_rel
    _save_config(cfg)
    print(f"已切换到: {args.name}")


def cmd_rename(args):
    """重命名小说。默认重命名当前活跃小说，也可指定任意小说。"""
    cfg = _load_config_rt()
    vault_path = Path(cfg["vault"]["path"])

    # 确定要重命名的小说
    if args.name:
        old_rel = f"novels/{args.name}"
    else:
        old_rel = cfg["vault"].get("novel", "")
        if not old_rel:
            print("错误：未指定小说名，且 config.yaml 中 vault.novel 未设置")
            print("用法: python main.py rename [小说名] --to 新名称")
            return

    old_path = vault_path / old_rel
    if not old_path.exists():
        print(f"错误：小说目录不存在: {old_path}")
        novels_dir = vault_path / "novels"
        if novels_dir.exists():
            existing = [d.name for d in novels_dir.iterdir() if d.is_dir()]
            if existing:
                print(f"现有小说: {', '.join(existing)}")
        return

    new_rel = f"novels/{args.to}"
    new_path = vault_path / new_rel
    if new_path.exists():
        print(f"错误：目标已存在: {new_path}")
        return

    old_path.rename(new_path)
    print(f"已重命名: {old_rel} → {new_rel}")

    # 如果重命名的是活跃小说，更新 config.yaml
    if old_rel == cfg["vault"].get("novel", ""):
        cfg["vault"]["novel"] = new_rel
        _save_config(cfg)
        print("config.yaml 已更新")


