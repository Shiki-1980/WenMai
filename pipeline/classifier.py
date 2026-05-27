"""实体分类器 —— 正则预筛 + LLM 精判（带上下文），无批量上限。

设计原则：
  1. 正则只处理高置信度模式（命名本身携带了足够信息）
  2. 不确定的留给 LLM（附带 source_context）
  3. 默认 fallback 为 "concept"（比 "person" 安全得多）
"""

from __future__ import annotations

import json
import re
from typing import Any


# ── 正则预筛规则 ────────────────────────────────────────────────

# 重要：只匹配"看起来几乎确定"的模式。模糊案例不在这里处理。
# 规则按优先级从高到低排列，首个匹配生效。

TYPE_RULES: list[tuple[str, list[str]]] = [
    # ── location：宗门、国家、城市、地区、建筑 ──
    ("location", [
        r".*(宗|派|门|寺|庙|观|庵|殿|阁|堂|院|庭|塔|堡|府|居|洞|墓|谷|峰|崖)$",
        r".*(国|帝国|王国|联邦|共和国|联盟国)$",
        r".*(城|镇|村|郡|州|省|区)$",
        r".*(域|界|大陆|岛|洲|半岛|群岛)$",
        r".*(山|山脉|海|洋|湖|河|江|原|野|林|漠|泽|渊)$",
        r".*(宫|阙|楼|台|亭|轩|榭|廊|园|苑)$",
        r".*(市|集|坊|街|巷|道|路|桥|渡|港|站)$",
        r"^(东|南|西|北|中|上|下|前|后|左|右|内|外).*(域|境|界|地|区)$",
    ]),

    # ── item：武器、法宝、丹药、材料、典籍 ──
    ("item", [
        r".*(剑|刀|枪|戟|斧|锤|鞭|弓|弩|盾|甲|铠|胄|盔)$",
        r".*(鼎|炉|镜|珠|玉|印|符|石|环|镯|佩|簪|钗|针|索|网|幡|旗)$",
        r".*(丹|药|丸|散|膏|液|汤|剂|粉)$",
        r".*(草|花|果|木|根|叶|藤|菌|芝|参)$",
        r".*(矿|石|晶|玉|铁|铜|银|金|钢|铁|陨)$",
        r".*(卷|册|图|令|牌|简|牍|书|典|谱|录|经|籍)$",
        r".*(袋|囊|瓶|罐|盒|匣|箱|柜|戒|环)$",
        r"^[《「『].*[》」』]$",  # 书名号包裹的功法典籍
    ]),

    # ── person：人物 ──
    ("person", [
        # 中文姓名模式：常见单姓 + 1-2字名
        r"^[赵钱孙李王张刘陈杨黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤尹黎易常武乔贺赖龚文]$",
        # 称号模式
        r".*(子|帝|皇|王|尊|圣|仙|神|魔|妖|佛|祖|宗|师|匠)$",
        # 西式人名
        r"^[A-Z][a-z]+[·‧][A-Z][a-z]+$",
    ]),

    # ── concept：功法、体系、境界、组织、种族、规则 ──
    ("concept", [
        r".*(诀|法|术|功|步|掌|拳|指|腿|爪|印|式|招|斩|击|破|灭|杀)$",
        r".*(境|界|期|层|级|阶|段|重|转|变)$",
        r".*(道|途|路|脉|络|系|统|制)$",
        r".*(力|气|元|能|灵|魂|神|识|念|意|势|场|域)$",
        r".*(族|盟|会|团|队|军|营|卫|司|局|院|部|处|所|站|社|帮|派)$",
        r".*(体|身|骨|肉|血|脉|瞳|翼|爪|角)$",  # 特殊体质、血脉
        r".*(阵|法|术|式|仪|祭|咒|印|纹|符|卦|诀)$",  # 阵法、仪式
        r".*(者|师|士|卫|兵|将|王|帝|皇|君|侯|卿|相|臣|民|奴|仆)$",  # 职业/身份
        r"^[《「『].*[》」』]$",  # 书名号（功法典籍更可能是 item，但这里作为兜底）
    ]),
]


def classify_by_regex(name: str) -> str | None:
    """用正则规则预筛实体类型。返回 type 或 None（不确定时）。

    规则按优先级排列：location > item > person > concept。
    首个匹配的规则生效。所有规则都不匹配返回 None，留给 LLM。
    """
    for etype, patterns in TYPE_RULES:
        for pattern in patterns:
            if re.search(pattern, name):
                return etype
    return None


# ── LLM 批量分类 ──────────────────────────────────────────────

BATCH_SIZE = 30  # 每批发给 LLM 的实体数


def _build_classify_system() -> str:
    return (
        "你是小说实体分类助手。为每个实体判断 type。\n"
        "type 只有四种：person, item, location, concept。\n\n"
        "分类标准：\n"
        "- person: 有智慧、有自主意识的生命体（人、妖、魔、神、精灵、兽人等）\n"
        "- item: 具体可持有的物件（武器、法宝、丹药、材料、功法秘籍书本、信物等）\n"
        "- location: 空间场所（宗门、城市、国家、秘境、山脉、洞府、房间等）\n"
        "- concept: 抽象设定（修炼境界、能量体系、组织势力、种族、规则、职业等）\n\n"
        '只输出 JSON: {"实体名": "type", ...}。不要任何前言、解释、markdown 代码块。'
    )


def _build_batch_prompt(entities: list[dict], worldview_excerpt: str) -> str:
    """构建一批实体的分类 prompt，附带 source_context。"""
    lines = []
    lines.append(f"世界观/主线摘要：\n{worldview_excerpt[:2000]}\n")
    lines.append("需要分类的实体（附上下文）：")
    for ent in entities:
        name = ent["name"]
        ctx = ent.get("source_context", "")[:150]
        src = ent.get("source_file", "")
        lines.append(f"- {name} | 来源: {src} | 上下文: ...{ctx}...")

    lines.append("\n输出 JSON 映射：")
    return "\n".join(lines)


def classify_batch_with_llm(
    gen,  # LLMGenerator
    entities: list[dict],
    worldview_text: str = "",
) -> dict[str, str]:
    """用 LLM 分批分类实体。返回 {name: type}。

    超过 BATCH_SIZE 的实体自动分批，每批 BATCH_SIZE 个。
    """
    all_results: dict[str, str] = {}

    # 先正则预筛
    uncertain: list[dict] = []
    for ent in entities:
        name = ent["name"]
        guessed = classify_by_regex(name)
        if guessed:
            all_results[name] = guessed
        else:
            uncertain.append(ent)

    # 对不确定的，分批调 LLM
    for i in range(0, len(uncertain), BATCH_SIZE):
        batch = uncertain[i : i + BATCH_SIZE]
        prompt = _build_batch_prompt(batch, worldview_text)
        raw = gen.generate(_build_classify_system(), prompt, json_mode=True)

        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start != -1 and end > start:
                batch_results = json.loads(raw[start:end])
                for name, etype in batch_results.items():
                    if etype in ("person", "item", "location", "concept"):
                        all_results[name] = etype
        except (json.JSONDecodeError, TypeError):
            # LLM 输出异常，对这批全用 concept
            for ent in batch:
                if ent["name"] not in all_results:
                    all_results[ent["name"]] = "concept"

    # 确保所有实体都有分类（fallback = concept）
    for ent in entities:
        if ent["name"] not in all_results:
            all_results[ent["name"]] = "concept"

    return all_results


def classify_all(
    gen,  # LLMGenerator
    entities: list[dict],
    worldview_text: str = "",
) -> list[dict]:
    """完整分类流程：正则预筛 + LLM 批量精判。

    接受 entity_list 格式的实体列表，返回带有 type 字段的完整列表。

    Args:
        gen: LLMGenerator 实例
        entities: [{"name": str, "source_file": str, "source_context": str, ...}, ...]
        worldview_text: 世界观和主线的文本上下文

    Returns:
        分类后的实体列表（每个 dict 包含 "type" 字段）
    """
    type_map = classify_batch_with_llm(gen, entities, worldview_text)

    classified = []
    for ent in entities:
        ent_copy = dict(ent)
        ent_copy["type"] = type_map.get(ent["name"], "concept")
        classified.append(ent_copy)

    # 统计
    counts = {"person": 0, "item": 0, "location": 0, "concept": 0}
    for ent in classified:
        counts[ent["type"]] = counts.get(ent["type"], 0) + 1
    regex_hits = sum(
        1 for e in classified
        if classify_by_regex(e["name"]) == e["type"]
    )
    print(f"    正则命中: {regex_hits}/{len(classified)} "
          f"| LLM 分类: {len(classified) - regex_hits} "
          f"| person={counts['person']} item={counts['item']} "
          f"location={counts['location']} concept={counts['concept']}")

    return classified
