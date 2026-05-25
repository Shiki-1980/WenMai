# WenMai（文脉）—— Obsidian + LLM 网文写作系统

实体驱动、wiki 化管理、长文不 OOC 的全自动写作管线。

## 核心理念

网文几百章，LLM 上下文装不下全部正文。系统的解法：

- **正文是「流」**：写完即蒸馏，不堆进 context
- **实体卡是「库」**：角色/物品/地点/世界观的当前状态持续累积，是唯一真相来源
- **世界观是「铁律」**：每章 context 始终加载顶层世界规则
- **按需检索**：`[[wikilink]]` 精确召回关联实体，比向量 RAG 更准

```
写第 N+1 章时，context =
  世界观.md（始终加载，≤5000 字）
  + 篇章约束 + 大纲
  + [[wikilink]] 关联的实体卡全文（人物/地点/物品/概念）
  + 最近 5 章摘要 + 上一章关键残留
  + 进行中的伏笔
```

## 目录结构

```
├── _templates/              # Obsidian 模板（共用）
├── pipeline/                # Python 管线代码
│   ├── config.yaml          #   配置
│   ├── main.py              #   入口
│   ├── reader.py / writer.py
│   ├── retriever.py / context_builder.py
│   ├── generator.py / distiller.py
│   └── prompts/             #   提示词模板
│
└── novels/                  # 每本小说独立目录
    └── 万古劫烬/
        ├── entity/          #   实体卡（图谱节点）
        │   ├── person/      #     角色
        │   ├── item/        #     物品/武器/丹药
        │   ├── location/    #     城镇/宗门/秘境
        │   └── concept/     #     功法/境界/势力概念
        ├── chapter/         #   章节正文
        ├── summary/         #   章节摘要
        ├── plot/            #   大纲 + 伏笔池 + 主线 + 世界观
        │   ├── 主线.md
        │   ├── 世界观.md    #     世界铁律（每章 context 始终加载）
        │   ├── 伏笔池.md
        │   └── arcs/
        └── index/           #   实体→章节倒排索引
```

## 安装

```bash
cd pipeline
pip install -r requirements.txt
```

## 配置

编辑 `pipeline/config.yaml`：

```yaml
vault:
  path: "/path/to/WenMai"        # 项目根目录
  novel: "novels/万古劫烬"        # 小说目录（相对于 path）

llm:
  provider: "deepseek"           # deepseek | anthropic | openai
  model: "deepseek-v4-flash"
  api_key: "${DEEPSEEK_API_KEY}" # 或直接填 key
  api_base: "https://api.deepseek.com"
  temperature: 0.8
  max_tokens: 16384

generation:
  chapter_words: 4000
```

开新书只需改 `vault.novel` 路径。

## 完整使用流程

网文分卷推进，每卷一个独立剧情单元。写完一卷后主角状态变了，基于最新状态规划下一卷。

### Step 1：创世三要素（唯一需要手写的）

在 Obsidian 中创建：

```
novels/万古劫烬/plot/世界观.md    ← 力量体系 + 世界铁律（模板：_templates/世界观.md）
novels/万古劫烬/entity/person/    ← 主角卡（模板：_templates/person.md）
novels/万古劫烬/plot/主线.md      ← 一句话核心冲突 + 第一卷大致方向
```

### Step 2：规划第一卷

```bash
# 章节数让 LLM 自行判断（-n 0），或手动指定（-n 25）
python main.py plan -v 1 -d "废柴逆袭，在青云宗站稳脚跟，参加外门大比" -n 0

# 字数是弹性的：高潮章 4000-6000，过渡章 2500-3500，铺垫章 1500-2500
```

生成 `plot/arcs/v01_001_025.md`（卷号在前）+ 所有相关实体卡。

### Step 3：审核大纲 → 写第一卷

```bash
python main.py write -a v01_001_025 -y
```

每章自动蒸馏：保存正文、更新实体状态、管理伏笔。

### Step 4：第一卷完结 → 规划第二卷

```bash
# 此时主角修为、持有物品、人际关系已不同
# LLM 基于最新实体状态生成更合理的大纲
python main.py plan -v 2 -d "离开宗门，前往中州参加武道大会" -n 0
python main.py write -a v02_030_060 -y
```

### Step 5：定期维护

```bash
python main.py enrich              # 增量刷新实体卡正文
python main.py status              # 查看分卷进度
python main.py audit               # LLM 审查一致性
```
- 更新倒排索引 → `index/`
- 添加/回收伏笔 → `plot/伏笔池.md`

### Step 6：定期 enrich

```bash
python main.py enrich
```

写 10-20 章后跑一次，用累积的剧情上下文增量更新实体卡正文（新能力、新关系、背景揭示）。

### Step 7：查看状态

```bash
python main.py status
```

输出：
```
已写章节: 15 章
实体总数: 48 (其中 stub: 3)

篇章大纲: 1 个
  青石寒蝉篇: 001-030 | 15/30 ████████░░░░░░░░ [writing]

  → 续写: `python main.py write -a arc_001_030`
  → 补全: `python main.py enrich`
```

## 命令参考

| 命令 | 用途 |
|------|------|
| `worldbuild` | 基于主线生成/更新世界观 |
| `plan -d "..." -n 30` | 生成篇章大纲 + 所有实体卡 |
| `write -a arc_001_030 [-y]` | 逐章写作（断点续写） |
| `write-one -c 5 -o "..."` | 写单独一章 |
| `enrich` | 增量更新实体卡 |
| `distill -c 15` | 重新蒸馏某章（手动改正文后） |
| `status` | 查看写作进度和实体状态 |

## 检索原理

```
写第 6 章 "雨夜追捕"：
  1. 大纲中 [[陆沉]] [[青石镇]] [[镇武司]] → 精确召回对应实体卡
  2. 陆沉卡 link 到 [[林霜]] [[寒蝉石]] → 一跳扩展，也召回
  3. concept 卡（武道九境、劫烬等）→ 正文 5000 字全文进入
  4. 世界观.md → 5000 字铁律始终加载
  5. 最近 5 章摘要 + 伏笔池 → 补充上下文

对比纯向量 RAG：
  "雨夜" 会召回一堆无关的雨景描写
  但召不回 "寒毒每 7 天发作一次" 这个关键设定
```

## 实体分类

| 类型 | 判断标准 | 示例 |
|------|----------|------|
| person | 有智慧的生命 | 人、妖、魔、兽、器灵 |
| item | 可持有/可使用的物件 | 武器、法宝、丹药、功法秘籍 |
| location | 空间场所 | 城镇、宗门、学院、秘境 |
| concept | 抽象设定 | 修炼境界、能量体系、制度规则 |

## 常见问题

**为什么不用向量 RAG？**
向量搜索按语义相似度排序，「金丹期战斗」和「筑基期战斗」语义高度相似，但角色状态完全不同。实体卡作为 single source of truth 保证状态精确。

**蒸馏错了怎么办？**
手动修改对应实体卡，然后 `python main.py distill -c N` 重新蒸馏。

**能换模型吗？**
编辑 `config.yaml` 的 `provider` 和 `model`。支持 DeepSeek、Anthropic、OpenAI 及所有 OpenAI 兼容 API。

**怎么开新书？**
改 `config.yaml` 的 `vault.novel`，在新目录下创建 `plot/主线.md` 和初始实体卡，从 Step 1 开始。

**会越写越崩吗？**
不会。每章蒸馏更新实体状态，`enrich` 定期刷新人设。世界观铁律始终在 context 中约束 LLM。
