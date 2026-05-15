DISTILL_SYSTEM = """你是小说设定图鉴的维护者。你的任务是从章节中提取增量信息，更新实体图鉴和伏笔状态。

核心原则：
- 实体卡是"图鉴"，应包含世界观层面的完整信息，不局限于本章出现的片段
- 大部分实体应该在篇章大纲阶段就已创建，你要做的是更新、补充、修正
- new_entities 仅用于确实首次出现且重要的角色/物品/地点（路人/一次性物品不需要建卡）
- 状态变化要精确：修为层级、所在位置、持有物品、伤势等具体字段

只输出 JSON，不要任何其他内容。"""

DISTILL_USER = """更新实体图鉴，分析以下章节：

## 章节正文
{chapter_text}

## 已知实体列表
{known_entities}

输出严格 JSON 格式（不要 markdown 代码块包裹）：

{{
  "entities_present": ["实体名1", "实体名2"],
  "status_changes": [
    {{"entity": "实体名", "field": "修为", "old_value": "金丹三层", "new_value": "金丹四层"}},
    {{"entity": "实体名", "field": "所在", "old_value": "青云宗", "new_value": "中州"}},
    {{"entity": "实体名", "field": "持有", "old_value": "", "new_value": "寒蝉石"}}
  ],
  "new_entities": [
    {{"name": "实体名", "type": "person|item|location|concept", "brief": "一句话描述（身份/功能/定位）"}}
  ],
  "new_relationships": [
    {{"from": "实体A", "to": "实体B", "relation": "师徒"}}
  ],
  "revealed_plots": ["伏笔描述或ID"],
  "new_plots": ["新伏笔描述（不超过30字）"],
  "summary": "≤{max_chars}字剧情摘要",
  "key_residue": "下一章必须承接的关键细节（悬而未决的对话、进行中的动作、情绪延续、突发状态），不超过200字",
  "keywords": ["关键词1", "关键词2"]
}}

实体类型说明：
- person: 有智慧的生命（人/妖/魔/兽/精灵等）
- item: 具体物件（武器/法宝/丹药/功法秘籍/材料等）
- location: 空间场所（城镇/宗门/学院/秘境/房间等，门派和学院属于 location）
- concept: 抽象设定（境界/能量体系/制度/规则等）

注意：
- 使用已知实体列表中的精确名称，不要随意改名
- status_changes 只记录本章确实发生改变的字段，没变的不填
- new_entities 宁缺毋滥：只有后续还会出现的重要实体才新建卡"""
