---
name: schema-constraints-rethink
description: Schema validation architecture pain points and improvement directions for novel_schema.json
type: project
---

Schema 约束环节需要重新设计。

## 当前架构

六个层次：
1. **novel_schema.json** — 定义每个 entity type 的 predicate（name, type, override policy, enum values）
2. **Observer** (temp 0.6) — LLM 自由文本观察章节变化
3. **Settler** (temp 0.25) — 将观察转为 JSON delta，选择 action (change/add/remove/append/append_description)
4. **State Validator** (temp 0.15) — 比较新旧状态，检测矛盾
5. **writer.apply_entity_delta** — 程序化校验 enum 值和 override policy
6. **state.json** — 事实的累积存储

## 已发现的痛点

1. **Enum 太刚性**: `身体状态` enum 只有 5 个值，LLM 输出"性高潮后疲惫"就报 WARN。故事中状态空间是开放的，enum 天然不够用。
2. **APPEND_ONLY 约束不透明**: Settler 不知道哪些字段是 APPEND_ONLY，容易用 `change` 替代 `append_description`。（已临时修复：distiller 新增 `_build_override_hint` 注入 prompt）
3. **未知实体**: 临时背景元素（出租车、未命名酒店）被当作实体提取，触发 "不在已知实体列表中" 警告。
4. **Validator 的 state-degraded 太极端**: 一次校验失败就放弃全部状态更新，实际上很多 WARN 级的问题不需要阻断。
5. **Schema 不演进**: 生成一次后不变。10 章、50 章后故事发展到新阶段，schema 的 enum 值可能过时。
6. **没有 severity 分级**: 当前只有 pass/fail，缺少 warn/error 分级。

## 可能的改进方向

- Schema 自演化：写完 N 章后让 LLM review 并扩充 enum values
- enum 改 string + 建议值（软约束而非硬约束）
- 实体重要性过滤（minor 实体不触发未知实体警告）
- Validator 分层：soft warnings（记录但不阻断）vs hard errors（要求重试）
- Schema 包含"不追踪实体"规则（如"出租车"类临时背景不建实体）

## 状态

用户在思考中，待讨论后制定具体方案。
