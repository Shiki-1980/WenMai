import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_vault():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for sub in (
            "entity/person", "entity/item", "entity/location", "entity/concept",
            "chapter", "summary", "plot", "index",
        ):
            (root / sub).mkdir(parents=True, exist_ok=True)
        yield root


@pytest.fixture
def vault_with_main_plot(tmp_vault):
    main_md = tmp_vault / "plot" / "主线.md"
    main_md.write_text(
        "---\ntitle: 测试主线\n---\n\n"
        "[[叶凡]]是天命之子，手持[[寒蝉石]]在[[青云宗]]修行。\n"
        "他修炼[[九劫天功]]，目标成为[[金丹强者]]。\n",
        encoding="utf-8",
    )
    return tmp_vault


@pytest.fixture
def vault_with_entity(tmp_vault):
    person_dir = tmp_vault / "entity" / "person"
    person_md = person_dir / "叶凡.md"
    person_md.parent.mkdir(parents=True, exist_ok=True)
    person_md.write_text(
        "---\n修为: 筑基巅峰\n身份: 青云宗外门弟子\n---\n\n"
        "叶凡，[[青云宗]]外门弟子，持有[[寒蝉石]]。\n",
        encoding="utf-8",
    )
    item_dir = tmp_vault / "entity" / "item"
    item_md = item_dir / "寒蝉石.md"
    item_md.parent.mkdir(parents=True, exist_ok=True)
    item_md.write_text(
        "---\n品阶: 地阶\n持有者: 叶凡\n---\n\n寒蝉石，一块散发着寒气的奇石。\n",
        encoding="utf-8",
    )
    return tmp_vault


@pytest.fixture
def sample_novel_schema():
    return {
        "novel": "测试小说",
        "schema_version": 1,
        "generated_at": "2025-01-01",
        "generated_by": "deepseek-v4-flash",
        "entity_schemas": {
            "person": {
                "label": "人物",
                "predicates": {
                    "修为": {"type": "enum", "category": "实力", "priority": 1,
                              "override": "override_allowed",
                              "values": ["凡人", "练气", "筑基", "金丹", "元婴"]},
                    "身份": {"type": "string", "category": "基础", "priority": 2},
                    "本命法宝": {"type": "string", "category": "持有", "priority": 5,
                               "override": "append_only"},
                },
            },
            "item": {
                "label": "物品",
                "predicates": {
                    "品阶": {"type": "enum", "category": "基础", "priority": 1,
                            "values": ["凡阶", "玄阶", "地阶", "天阶", "仙阶"]},
                    "持有者": {"type": "string", "category": "基础", "priority": 2},
                },
            },
        },
    }


@pytest.fixture
def schema_json(tmp_vault, sample_novel_schema):
    schema_file = tmp_vault / "novel_schema.json"
    schema_file.write_text(json.dumps(sample_novel_schema, ensure_ascii=False), encoding="utf-8")
    return schema_file
