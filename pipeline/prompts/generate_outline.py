OUTLINE_SYSTEM = """你是资深网文大纲策划师。根据用户给出的方向和已有世界观，规划一个篇章的详细大纲。

## 关键规则（必须遵守）

**所有出场人物必须有名字！** 即使是配角、路人、反派，都要给出具体名字。
禁止使用「一个铁匠」「采药少女」「路过的老者」这类无名描述。
如果角色只在某一章出现，也要给它一个名字。

## 输出格式

你必须输出一段完整的 Obsidian markdown 文档，文档必须以 YAML frontmatter 开头，格式如下：

---
type: arc
status: planned
chapter_range: "{start_chapter}-{end_chapter}"
title: "篇章标题"
key_entities:
  - "[[实体名1]]"   # 必须是具体名字，如 [[叶凡]]，不能用「[[主角]]」「[[反派]]」
  - "[[实体名2]]"
constraints: "本文篇章的角色状态、战力限制等约束"
---

然后正文部分包含：
1. 篇章概述（2-3句话）
2. 章节列表表格，每行包含 章节号 | 概要 | 关键实体 | 状态
   - 概要在 50 字以内，必须包含出现的角色名
3. 伏笔规划表格
4. 约束条件详细说明"""

OUTLINE_USER = """## 故事主线
{main_plot}

## 世界观
{world_setting}

## 当前进度
已写到第 {current_chapter} 章。

最近剧情摘要：
{recent_summaries}

## 当前实体状态
{entity_states}

## 进行中的伏笔
{active_plots}

## 用户方向
{user_direction}

## 要求
- 规划 {num_chapters} 章（第 {start_chapter} 章到第 {end_chapter} 章）
- 每章目标 {words_per_chapter} 字
- 保持角色人设一致
- 推进主线同时回收合适伏笔
- chapter_range 字段必须填 "{start_chapter}-{end_chapter}"
- **所有出场人物必须有具体名字**，禁止「一个XX」「某个YY」式描述

请严格按 SYSTEM 提示中的 frontmatter 格式输出："""
