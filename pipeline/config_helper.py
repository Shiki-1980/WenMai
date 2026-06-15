"""配置加载和路径解析 —— 供 main.py 和 commands/* 共享。"""

import sys
from pathlib import Path

import ruamel.yaml as _ryaml
import yaml as _yaml_lib

CONFIG = Path(__file__).parent / "config.yaml"


def load_config():
    with open(CONFIG) as f:
        return _yaml_lib.safe_load(f)


def load_config_rt():
    yaml_rt = _ryaml.YAML()
    yaml_rt.preserve_quotes = True
    with open(CONFIG) as f:
        return yaml_rt.load(f)


def save_config(cfg):
    yaml_rt = _ryaml.YAML()
    yaml_rt.width = 200
    yaml_rt.indent(mapping=2, sequence=2, offset=2)
    with open(CONFIG, "w") as f:
        yaml_rt.dump(cfg, f)


def get_paths(novel_override: str | None = None):
    """返回 (content_root, template_dir, vault_path)。确保小说目录结构存在。"""
    cfg = load_config()
    vault_path = Path(cfg["vault"]["path"])
    novel_rel = novel_override or cfg["vault"].get("novel", "")
    if not novel_rel:
        print("错误：未指定小说。使用 --novel <名称> 或设置 config.yaml 中的 vault.novel")
        sys.exit(1)

    content_root = vault_path / novel_rel
    template_dir = vault_path / "_templates"

    for sub in [
        "entity/person", "entity/item", "entity/location", "entity/concept",
        "chapter", "summary", "plot/arcs", "index",
    ]:
        (content_root / sub).mkdir(parents=True, exist_ok=True)

    return content_root, template_dir, vault_path


def _auto_detect_volume(reader) -> int:
    """从已有 arc 自动检测下一卷号。"""
    max_vol = 0
    for arc_name in reader.list_arcs():
        arc = reader.read_arc(arc_name)
        if arc:
            vol = arc[0].get("volume", 0)
            if isinstance(vol, int) and vol > max_vol:
                max_vol = vol
    return max_vol + 1 if max_vol > 0 else 1
