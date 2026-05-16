"""Markdown 渲染器 —— 从 state.json + 模板渲染 Obsidian 实体卡。

JSON 是权威源，markdown 是投影。每次 state.json 更新后，
此模块重新渲染对应的实体 markdown 文件。

模板语法（简单替换，不引入 Jinja2）：
  {{变量}}            → 替换为 fact 的 object 值
  {{#each category}}  → 按 category 分组遍历 facts
  {{/each}}
  {{tags}}            → 逗号分隔的 Obsidian tags
  {{importance_tag}}  → 根据 importance 映射的 tag
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from state_schema import EntityState, NovelSchema, PredicateDef


# ── 默认模板（当 schema 未定义模板时的 fallback）───────────────

DEFAULT_TEMPLATES: dict[str, str] = {
    "person": """---
type: person
status: {{active_status}}
importance: {{importance}}
tags: [{{tags}}]
created: {{date}}
updated: {{date}}
---
# {{name}}

## 基础信息
{{#each 基础}}
- {{predicate}}：{{object}}
{{/each}}

## 当前状态
{{#each 状态}}
- {{predicate}}：{{object}}
{{/each}}

## 实力
{{#each 实力}}
- {{predicate}}：{{object}}
{{/each}}

## 能力
{{#each 能力}}
- {{predicate}}：{{object}}
{{/each}}

## 资源
{{#each 资源}}
- {{predicate}}：{{object}}
{{/each}}

## 关系
{{#each 关系}}
- {{object}}
{{/each}}

## 动机
{{#each 动机}}
- {{predicate}}：{{object}}
{{/each}}
""",

    "item": """---
type: item
status: {{active_status}}
tags: [{{tags}}]
created: {{date}}
updated: {{date}}
---
# {{name}}

## 属性
{{#each 基础}}
- {{predicate}}：{{object}}
{{/each}}

## 当前状态
{{#each 状态}}
- {{predicate}}：{{object}}
{{/each}}
""",

    "location": """---
type: location
status: {{active_status}}
tags: [{{tags}}]
created: {{date}}
updated: {{date}}
---
# {{name}}

## 基本信息
{{#each 基础}}
- {{predicate}}：{{object}}
{{/each}}

## 位置层级
{{#each 位置}}
- {{predicate}}：{{object}}
{{/each}}

## 当前状态
{{#each 状态}}
- {{predicate}}：{{object}}
{{/each}}
""",

    "concept": """---
type: concept
status: {{active_status}}
tags: [{{tags}}]
created: {{date}}
updated: {{date}}
---
# {{name}}

## 定义
{{#each 基础}}
- {{predicate}}：{{object}}
{{/each}}

## 详情
{{#each 状态}}
- {{predicate}}：{{object}}
{{/each}}
""",
}


class MarkdownRenderer:
    """从 state.json + schema 模板渲染 Obsidian markdown。"""

    def __init__(self, schema: NovelSchema | None = None):
        self.schema = schema

    def render_entity_body(
        self,
        state: EntityState,
        importance: str = "major",
        active_status: str = "active",
    ) -> str:
        """渲染实体卡 body（不含 frontmatter）。

        Args:
            state: 实体状态
            importance: protagonist/major/supporting/minor
            active_status: active/injured/dead 等

        Returns:
            markdown body（从标题开始，无 frontmatter）
        """
        entity_type = state.entity_type
        template = self._get_template(entity_type)
        # 去掉模板中的 frontmatter 部分
        body_template = _strip_frontmatter_from_template(template)
        grouped = self._group_facts_by_category(state)
        variables = self._build_variables(state, importance, active_status, entity_type)
        return self._render_template(body_template, variables, grouped)

    def render_frontmatter(self, state: EntityState, importance: str = "major") -> dict:
        """生成 Obsidian frontmatter 字典。"""
        etype = state.entity_type
        active = state.get_all_active_facts()

        fm = {
            "type": etype,
            "status": active.get("状态", "active"),
            "importance": importance,
        }

        # 从 schema 获取 tags
        if self.schema:
            es = self.schema.get_entity_schema(etype)
            if es:
                fm["tags"] = list(es.tags)
                # 重要度 tag
                tag_map = getattr(es, 'importance_tag_map', {}) if hasattr(es, 'importance_tag_map') else {}
                if not tag_map:
                    tag_map = {
                        "protagonist": "#protagonist",
                        "major": "#major-character",
                        "supporting": "#supporting",
                        "minor": "#minor",
                    }
                imp_tag = tag_map.get(importance)
                if imp_tag:
                    fm["tags"].append(imp_tag)

        # 将 type="list" 的 facts 渲染为数组
        for pred_name, val in active.items():
            pdef = self.schema.get_predicate_def(etype, pred_name) if self.schema else None
            if pdef and pdef.type == "list":
                # 同名 predicate 的多个 fact → 数组
                facts = [f for f in state.get_active_facts_list() if f.predicate == pred_name]
                if len(facts) > 1:
                    fm[pred_name] = [f.object for f in facts]
                else:
                    fm[pred_name] = val
            else:
                fm[pred_name] = val

        fm["updated"] = datetime.now().strftime("%Y-%m-%d")
        return fm

    # ── 内部方法 ──

    def _get_template(self, entity_type: str) -> str:
        """获取模板（优先 schema，回退默认）。"""
        if self.schema:
            es = self.schema.get_entity_schema(entity_type)
            if es and es.markdown_template:
                return es.markdown_template
        return DEFAULT_TEMPLATES.get(entity_type, DEFAULT_TEMPLATES["person"])

    def _group_facts_by_category(self, state: EntityState) -> dict[str, list["EntityFact"]]:
        """按 category 分组有效事实。"""
        groups: dict[str, list] = {}
        entity_type = state.entity_type

        for fact in state.get_active_facts_list():
            # 从 schema 获取 category
            if self.schema:
                pdef = self.schema.get_predicate_def(entity_type, fact.predicate)
                cat = pdef.category if pdef else "其他"
            else:
                cat = "其他"
            if cat not in groups:
                groups[cat] = []
            groups[cat].append(fact)

        return groups

    def _build_variables(
        self, state: EntityState, importance: str, active_status: str, entity_type: str,
    ) -> dict[str, str]:
        """构建模板变量字典。"""
        vars = {
            "name": state.entity,
            "importance": importance,
            "active_status": active_status,
            "date": datetime.now().strftime("%Y-%m-%d"),
        }

        # Tags
        tags = []
        if self.schema:
            es = self.schema.get_entity_schema(entity_type)
            if es:
                tags.extend(es.tags)
        vars["tags"] = ", ".join(tags)

        # Importance tag
        imp_tag_map = {
            "protagonist": "#protagonist",
            "major": "#major-character",
            "supporting": "#supporting",
            "minor": "#minor",
        }
        vars["importance_tag"] = imp_tag_map.get(importance, "")

        # 每个 fact 的 object 值作为 {{predicate}} 变量
        for fact in state.get_active_facts_list():
            vars[fact.predicate] = fact.object

        return vars

    def _render_template(
        self,
        template: str,
        variables: dict[str, str],
        grouped: dict[str, list],
    ) -> str:
        """渲染模板。

        处理两种语法：
          {{variable}} → 替换
          {{#each category}} ... {{/each}} → 分组遍历
        """
        result = []
        lines = template.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # 检测 {{#each category}}
            each_match = _EACH_RE.match(line)
            if each_match:
                cat = each_match.group(1).strip()
                facts = grouped.get(cat, [])
                # 收集 until {{/each}}
                i += 1
                block_lines = []
                while i < len(lines) and "{{/each}}" not in lines[i]:
                    block_lines.append(lines[i])
                    i += 1
                # 渲染 block 中的每一行
                for fact in facts:
                    for bl in block_lines:
                        rendered = bl.replace("{{predicate}}", fact.predicate)
                        rendered = rendered.replace("{{object}}", fact.object)
                        result.append(rendered)
                i += 1  # skip {{/each}}
                continue

            # 普通变量替换
            for var_name, var_value in variables.items():
                placeholder = "{{" + var_name + "}}"
                line = line.replace(placeholder, str(var_value) if var_value else "")

            # 跳过包含未替换 {{...}} 的行（schema 定义的谓词在当前 state 中没有值）
            if "{{" not in line or "{{" not in line:
                result.append(line)

            i += 1

        return "\n".join(result)


import re
_EACH_RE = re.compile(r"^\s*\{\{#each\s+(.+?)\}\}\s*$")


def _strip_frontmatter_from_template(template: str) -> str:
    """从模板中移除 frontmatter 部分（--- ... ---）。"""
    lines = template.split("\n")
    if not lines or not lines[0].strip().startswith("---"):
        return template
    # 找到闭合的 ---
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:])
    return template
