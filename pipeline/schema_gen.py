"""Schema 生成器 —— LLM 从世界观自动生成 novel_schema.json。"""

import json
from pathlib import Path

from state_schema import NovelSchema, SCHEMA_FILENAME

SCHEMA_GEN_SYSTEM = """你是小说世界观架构师和数据结构设计师。你的任务是分析一部小说的世界观设定，生成一份结构化的 `novel_schema.json`。

这份 schema 用于：
1. 定义每种实体类型（人物/物品/地点/概念）有哪些属性（谓词）
2. 为枚举型属性定义允许值列表（如修为境界）
3. 定义每个属性的覆盖策略（锁定/只追加/可覆盖）
4. 生成 markdown 渲染模板

## 实体类型（固定四种）

- person: 有智慧的生命（人/妖/魔/兽/精灵等）
- item: 具体物件（武器/法宝/丹药/功法秘籍/材料/信物等）
- location: 空间场所（城镇/宗门/学院/秘境/房间/洞府等）
- concept: 抽象设定（境界/能量体系/制度/规则/组织势力等）

## 谓词设计原则

1. 从世界观中提取所有对剧情有影响的属性
2. 每个谓词需要 type（enum/string/list）、category（分组）、priority（1-99排序）、description
3. enum 类型必须列出所有允许值（从世界观中提取）
4. 通用属性（如"状态"、"目标"）每种类型都应有
5. 该小说特有的属性（如修仙的"修为"、科幻的"科技等级"、都市的"异能等级"）必须捕捉

## Override Policy 设计原则

- locked: 一旦设定不可改。包括：血脉/种族、天赋异能、力量体系根基、世界规则
- append_only: 只能新增不能删改。包括：功法/技能列表、禁用模式、关系
- override_allowed: 章节级可修改（默认）。包括：修为、所在、持有、状态、目标

## Markdown 模板

为每种实体类型生成一个简洁的模板，使用 {{变量}} 语法：
- {{name}} 实体名
- {{predicate_name}} 该谓词的当前值（如 {{修为}}、{{所在}}）
- {{#each category_name}} ... {{/each}} 按 category 分组遍历
- {{tags}} 逗号分隔的标签
- {{importance_tag}} 重要度标签

只输出 JSON，不要任何其他内容。"""

SCHEMA_GEN_USER = """请为以下小说生成 novel_schema.json。

## 世界观设定
{world_setting}

## 已有 Concept 卡（世界观相关设定）
{concept_cards}

## 已有实体卡摘要（人物/地点/物品）
{entity_summary}

## 输出格式

```json
{{
  "novel": "小说名",
  "schema_version": 2,
  "generated_at": "日期",
  "generated_by": "LLM: 模型名",

  "override_policy": {{
    "locked": ["route.primary_genre", "world_rules.power_system"],
    "append_only": ["anti_patterns"]
  }},

  "retrieval": {{
    "min_recall": 3,
    "max_hop": 1,
    "max_entity_cards": 30
  }},

  "entity_schemas": {{
    "person": {{
      "label": "人物",
      "predicates": {{
        "修为": {{
          "type": "enum",
          "values": ["境界1", "境界2", ...],
          "category": "实力",
          "priority": 1,
          "override": "override_allowed",
          "description": "当前修为境界"
        }},
        "血脉": {{
          "type": "string",
          "category": "天赋",
          "priority": 6,
          "override": "locked",
          "description": "血统/种族/特殊体质"
        }}
        // ... 其他谓词
      }},
      "tags": ["#character"],
      "importance_tag_map": {{
        "protagonist": "#protagonist",
        "major": "#major-character",
        "supporting": "#supporting",
        "minor": "#minor"
      }},
      "markdown_template": "---\\ntype: person\\nstatus: {{{{active_status}}}}\\nimportance: {{{{importance}}}}\\ntags: [{{{{tags}}}}]\\n---\\n\\n# {{{{name}}}}\\n\\n## 基础信息\\n- 身份：{{{{身份}}}}\\n\\n## 当前状态\\n{{{{#each 状态}}}}\\n- {{{{predicate}}}}：{{{{object}}}}\\n{{{{/each}}}}\\n\\n## 实力\\n{{{{#each 实力}}}}\\n- {{{{predicate}}}}：{{{{object}}}}\\n{{{{/each}}}}\\n\\n## 能力\\n{{{{#each 能力}}}}\\n- {{{{predicate}}}}：{{{{object}}}}\\n{{{{/each}}}}\\n\\n## 关系\\n{{{{#each 关系}}}}\\n- {{{{object}}}}\\n{{{{/each}}}}"
    }},
    "item": {{ /* 同上结构 */ }},
    "location": {{ /* 同上结构 */ }},
    "concept": {{ /* 同上结构 */ }}
  }}
}}
```

要求：
1. 从世界观中提取所有可枚举值（境界/血脉类型/势力/地点等）
2. person 必须有：修为(如果有修炼体系)/身份/所在/持有/功法/天赋/技能/关系/目标/身体状态
3. 该小说独有的属性（灵根/异能/科技等级/超能力等）必须纳入
4. person 类型必须包含 "详细描述" 谓词（type: string, category: 描述, override: append_only, priority: 99）
   - 描述人物的外貌、性格、背景故事等。每次章节可追加新信息。
5. markdown_template 中的 {{变量}} 必须与 predicates 中的 key 一一对应
6. 不要省略任何 entity_schema 类型（四种都要有）

请输出 JSON："""


def generate_schema(generator, reader, novel_name: str) -> NovelSchema | None:
    """调用 LLM 生成 novel_schema.json。

    Args:
        generator: LLMGenerator 实例
        reader: VaultReader 实例
        novel_name: 小说名

    Returns:
        NovelSchema 或 None（生成失败时）
    """
    # 收集上下文
    world = reader.read_world_bible()
    world_text = world[1][:5000] if world else "（暂无世界观设定）"

    # Concept 卡
    concept_parts = []
    for p in sorted(reader.concept_dir.glob("*.md")):
        card = reader._read_md(p)
        if card:
            _, body = card
            concept_parts.append(f"### {p.stem}\n{body[:2000]}")
    concept_text = "\n\n".join(concept_parts) if concept_parts else "（暂无 concept 卡）"

    # 实体摘要
    entity_lines = []
    for etype, name in reader.all_entity_names():
        card = reader.read_entity(etype, name)
        if card:
            meta, body = card
            entity_lines.append(f"- [{etype}] {name}: {meta.get('status', '?')}")
    entity_text = "\n".join(entity_lines) if entity_lines else "（暂无实体卡）"

    prompt = SCHEMA_GEN_USER.format(
        world_setting=world_text,
        concept_cards=concept_text,
        entity_summary=entity_text,
    )

    print("正在调用 LLM 生成 novel_schema.json ...")
    raw = generator.generate(SCHEMA_GEN_SYSTEM, prompt, json_mode=True)

    if not raw:
        print("  [ERROR] LLM 返回空结果")
        return None

    try:
        # 尝试解析 JSON
        data = _extract_json(raw)
        if data is None:
            print("  [ERROR] 无法从 LLM 输出中解析 JSON")
            print(f"  原始输出前 200 字: {raw[:200]}")
            return None

        # 补全必要字段
        data.setdefault("novel", novel_name)
        data.setdefault("schema_version", 2)
        data.setdefault("override_policy", {"locked": [], "append_only": []})
        data.setdefault("retrieval", {"min_recall": 3, "max_hop": 1, "max_entity_cards": 30})

        schemas = data.setdefault("entity_schemas", {})
        for etype in ["person", "item", "location", "concept"]:
            if etype not in schemas:
                schemas[etype] = {
                    "label": {"person": "人物", "item": "物品", "location": "地点", "concept": "概念"}[etype],
                    "predicates": {},
                    "tags": [],
                    "markdown_template": "",
                }

        schema = NovelSchema.from_dict(data)
        return schema

    except Exception as e:
        print(f"  [ERROR] Schema 解析失败: {e}")
        return None


def _extract_json(raw: str) -> dict | None:
    """从 LLM 输出中提取 JSON 对象。"""
    import re
    raw = raw.strip()

    # 去掉可能的 markdown 代码块
    m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", raw)
    if m:
        raw = m.group(1).strip()

    # 找到最外层 JSON 对象
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:i + 1])
    return None


def init_schema_for_novel(generator, reader, content_root: Path, force: bool = False) -> NovelSchema | None:
    """为当前小说初始化 schema。如果已有 schema 且不 force，直接返回已有。

    Args:
        generator: LLMGenerator 实例
        reader: VaultReader 实例
        content_root: 小说内容根目录 (novels/<name>/)
        force: 是否强制重新生成

    Returns:
        NovelSchema 或 None
    """
    schema_path = content_root / SCHEMA_FILENAME

    if schema_path.exists() and not force:
        print(f"novel_schema.json 已存在，使用 --force 覆盖")
        return NovelSchema.load(content_root)

    # 推断小说名
    novel_name = content_root.name

    schema = generate_schema(generator, reader, novel_name)
    if schema:
        schema.generated_at = __import__('datetime').datetime.now().strftime("%Y-%m-%d")
        schema.generated_by = f"LLM: {generator.model}"
        schema.save(content_root)
        print(f"novel_schema.json 已保存到: {schema_path}")
        print(f"  人物谓词: {list(schema.get_predicates('person').keys())}")
        print(f"  物品谓词: {list(schema.get_predicates('item').keys())}")
        print(f"  地点谓词: {list(schema.get_predicates('location').keys())}")
        print(f"  概念谓词: {list(schema.get_predicates('concept').keys())}")
        return schema

    return None
