"""Writer Agent 工具集 —— LLM 在写作过程中按需调用的检索工具。

三个工具：
  lookup_entity(name)      → 查实体当前状态
  lookup_recent_events(n)  → 查最近章节摘要 + 伏笔
  check_world_rules(topic) → 查世界观规则

设计原则：
  - 返回精简信息（200-500字），不是完整实体卡
  - 包含 LLM 写作时最需要的关键数据
  - 零外部依赖
"""

from __future__ import annotations

import json

# ── Tool definitions (OpenAI function calling format) ──────────

WRITER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_entity",
            "description": "查询一个实体（角色/物品/地点/概念）的当前状态。写作中遇到任何实体时都应先调用此工具确认其最新状态。支持正式名和别名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "实体名称（可以是正式名或别名，如'陆沉'、'弃子拾三'、'折雪断剑'）"
                    }
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_recent_events",
            "description": "查询最近几章的剧情摘要、当前伏笔状态和上一章的关键残留。开始写作前和需要确认剧情进度时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "需要查看最近几章的摘要（默认3，最多10）"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_world_rules",
            "description": "查询世界观规则：力量体系、地理、势力格局、世界法则等。写作涉及任何世界设定时需要确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "需要查询的规则主题，如'修为体系'、'地理'、'势力'、'物品等级'、'禁忌'。留空返回完整世界观摘要。"
                    }
                }
            }
        }
    }
]


# ── Tool implementations ─────────────────────────────────────

class ToolExecutor:
    """执行工具调用，返回精简的结构化信息。"""

    def __init__(self, reader, chapter_context: dict | None = None):
        """
        Args:
            reader: VaultReader 实例
            chapter_context: {
                "chapter_number": int,
                "protagonist_name": str,
            }
        """
        self.reader = reader
        self.ctx = chapter_context or {}
        self._entity_cache: dict[str, str] = {}  # 会话内缓存，避免重复查同一实体

    def execute(self, tool_name: str, arguments: dict) -> str:
        """执行一个工具调用，返回文本结果。"""
        if tool_name == "lookup_entity":
            return self._lookup_entity(arguments.get("name", ""))
        elif tool_name == "lookup_recent_events":
            return self._lookup_recent_events(arguments.get("count", 3))
        elif tool_name == "check_world_rules":
            return self._check_world_rules(arguments.get("topic", ""))
        else:
            return f"未知工具: {tool_name}"

    def _lookup_entity(self, name: str) -> str:
        """查实体当前状态。"""
        if not name:
            return "错误：请提供实体名称"

        # 检查缓存
        if name in self._entity_cache:
            return self._entity_cache[name]

        # 1. 精确匹配实体卡
        entity_type, state, card = self._find_entity(name)

        if not card:
            return f"未找到名为'{name}'的实体。这可能是一个新角色/物品，后续蒸馏会自动创建实体卡。"

        meta, body = card
        etype = entity_type or meta.get("type", "person")

        # 2. 构建精简返回
        lines = [f"## {name} [{etype}]"]

        # 从 state.json 获取最新状态
        if state and state.facts:
            active = state.get_all_active_facts()
            # 按重要性排列
            priority = ["修为", "身份", "所在", "持有", "状态", "目标", "身体状态",
                        "功法", "天赋", "技能", "血脉", "关系"]
            for pred in priority:
                if pred in active:
                    lines.append(f"- {pred}：{active[pred]}")
            for pred, val in active.items():
                if pred not in priority:
                    lines.append(f"- {pred}：{val}")

        # 从 frontmatter 补充
        if meta.get("importance"):
            lines.append(f"- 重要度：{meta['importance']}")
        if meta.get("status"):
            lines.append(f"- 活跃状态：{meta['status']}")

        # 近期出现章节
        chapters = self.reader.summaries_for_entity(name)
        if chapters:
            recent = chapters[-5:]
            lines.append(f"- 出现章节：{recent}")

        # 关联实体（wikilinks）
        links = meta.get("_links", [])
        if links:
            main_links = [link for link in links if link in _get_main_entities(self.reader)][:8]
            if main_links:
                lines.append(f"- 关联实体：{', '.join(main_links)}")

        result = "\n".join(lines)
        self._entity_cache[name] = result
        return result

    def _lookup_recent_events(self, count: int = 3) -> str:
        """查最近章节摘要 + 伏笔 + 上一章残留。"""
        count = max(1, min(count, 10))
        chapter_number = self.ctx.get("chapter_number", 0)

        lines = []

        # 最近 N 章摘要
        summaries = self.reader.recent_summaries(count, chapter_number)
        if summaries:
            lines.append("## 最近剧情")
            for ch_num, meta, body in summaries:
                lines.append(f"### 第{ch_num}章")
                lines.append(body[:300])
                residue = meta.get("key_residue", "")
                if residue and ch_num == chapter_number - 1:
                    lines.append(f"**上一章关键残留**：{residue}")

        # 伏笔
        plot = self.reader.read_plot_pool()
        if plot:
            _, body = plot
            lines.append("\n## 当前伏笔池")
            # 只取进行中的伏笔（前 1000 字）
            active_section = body.split("## 已回收")[0] if "## 已回收" in body else body[:2000]
            lines.append(active_section[:1500])

        return "\n".join(lines) if len(lines) > 1 else "暂无历史章节数据"

    def _check_world_rules(self, topic: str = "") -> str:
        """查世界观规则。"""
        world = self.reader.read_world_bible()
        if not world:
            return "世界观文件尚未创建。请基于主线运行 worldbuild 生成。"

        _, body = world

        if topic:
            # 尝试找相关 section
            sections = body.split("\n## ")
            relevant = [s for s in sections if topic in s]
            if relevant:
                return f"## 世界观 - {topic}\n\n" + "\n## ".join(relevant)[:2000]
            else:
                return f"世界观中未找到与'{topic}'直接相关的段落。完整世界观摘要：\n\n{body[:1500]}"
        else:
            return f"## 世界观摘要\n\n{body[:2000]}"

    def _find_entity(self, name: str) -> tuple[str | None, object | None, tuple | None]:
        """查找实体：返回 (entity_type, state, card)。"""
        from state_schema import load_entity_state

        # 尝试四种类型
        for etype in ["person", "item", "location", "concept"]:
            card = self.reader.read_entity(etype, name)
            if card:
                state_path = self.reader.entity_state_path(etype, name)
                state = load_entity_state(state_path)
                return etype, state, card

        # 别名匹配：查 alias_index
        alias_path = self.reader.root / "index" / "entity_alias_index.json"
        if alias_path.exists():
            aliases_data = json.loads(alias_path.read_text("utf-8"))
            for canonical, entry in aliases_data.get("entities", {}).items():
                all_patterns = [entry.get("canonical", "")] + entry.get("aliases", []) + entry.get("auto_aliases", [])
                if name in all_patterns:
                    # 找到了，用 canonical 再查
                    for etype in ["person", "item", "location", "concept"]:
                        card = self.reader.read_entity(etype, canonical)
                        if card:
                            state_path = self.reader.entity_state_path(etype, canonical)
                            state = load_entity_state(state_path)
                            return etype, state, card

        return None, None, None


def _get_main_entities(reader) -> set[str]:
    """获取主要实体名（importance 为 protagonist/major 的）。"""
    mains = set()
    for etype, name in reader.all_entity_names():
        card = reader.read_entity(etype, name)
        if card:
            meta, _ = card
            imp = meta.get("importance", "")
            if imp in ("protagonist", "major"):
                mains.add(name)
    return mains
