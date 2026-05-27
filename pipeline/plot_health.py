"""伏笔健康追踪 —— 分析伏笔池状态，检测积压和异常。

提供：
  - PlotThreadHealth: 单个伏笔的健康指标
  - analyze_plot_health(): 从伏笔池 JSON 分析所有伏笔状态
  - build_plot_context(): 生成注入写作提示的伏笔摘要文本
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PlotThreadHealth:
    """单个伏笔的健康状态。"""
    plot_id: str
    description: str
    planted_chapter: int
    last_advanced_chapter: int
    idle_chapters: int              # 已闲置多少章未推进
    total_chapters_since_planted: int
    status: str                     # 进行中 / 积压 / 可回收 / 已回收
    urgency: str                    # normal / warning / critical
    entities: list[str] = field(default_factory=list)

    # 阈值
    IDLE_WARNING = 8    # 闲置 8 章 → warning
    IDLE_CRITICAL = 15  # 闲置 15 章 → critical
    AGE_CRITICAL = 30   # 埋下 30 章未回收 → critical

# 伏笔池容量
MAX_ACTIVE_PLOTS = 15       # 活跃伏笔上限，超过触发警告
PLOTS_WARNING_THRESHOLD = 12  # 超过此数建议回收而非新增


def parse_plot_pool_json(json_path: Path) -> list[dict]:
    """读取伏笔池 JSON 文件。不存在返回空列表。"""
    if not json_path.exists():
        return []
    data = json.loads(json_path.read_text("utf-8"))
    return data.get("threads", [])


def parse_plot_pool_markdown(md_path: Path) -> list[dict]:
    """从 markdown 表格解析伏笔池（向后兼容旧格式）。

    表格格式: | ID | 描述 | 埋下章节 | 涉及实体 | 预计回收 | 状态 |
    """
    if not md_path.exists():
        return []
    content = md_path.read_text("utf-8")

    threads = []
    in_table = False
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("|---"):
            in_table = True
            continue
        if in_table and line.startswith("|") and not line.startswith("| ID"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 3:
                pid = cells[0]
                desc = cells[1] if len(cells) > 1 else ""
                planted_str = cells[2] if len(cells) > 2 else ""
                planted = 0
                m = re.search(r"ch_?(\d+)", planted_str)
                if m:
                    planted = int(m.group(1))
                status = cells[-1] if len(cells) > 1 else "进行中"
                entities_str = cells[3] if len(cells) > 3 else ""
                entities = [e.strip() for e in entities_str.split(",") if e.strip()]

                threads.append({
                    "id": pid,
                    "description": desc,
                    "planted_chapter": planted,
                    "last_advanced_chapter": planted,
                    "entities": entities,
                    "status": status,
                })
        elif in_table and not line.startswith("|"):
            in_table = False

    return threads


def analyze_plot_health(threads: list[dict], current_chapter: int) -> list[PlotThreadHealth]:
    """分析所有伏笔的健康状态。"""
    results = []
    for t in threads:
        if t.get("status") == "已回收":
            continue

        planted = t.get("planted_chapter", 0)
        last_adv = t.get("last_advanced_chapter", planted)
        idle = current_chapter - last_adv if last_adv > 0 else current_chapter - planted
        total_age = current_chapter - planted if planted > 0 else 0

        # 判断积压程度
        if idle >= PlotThreadHealth.IDLE_CRITICAL:
            urgency = "critical"
            status = "⚠️严重积压"
        elif idle >= PlotThreadHealth.IDLE_WARNING:
            urgency = "warning"
            status = "⚡轻度积压"
        else:
            urgency = "normal"
            status = "进行中"

        # 埋了太久没回收
        if total_age >= PlotThreadHealth.AGE_CRITICAL and urgency == "normal":
            urgency = "warning"
            status = "⏳长期未回收"

        results.append(PlotThreadHealth(
            plot_id=t["id"],
            description=t.get("description", ""),
            planted_chapter=planted,
            last_advanced_chapter=last_adv,
            idle_chapters=idle,
            total_chapters_since_planted=total_age,
            status=status,
            urgency=urgency,
            entities=t.get("entities", []),
        ))

    # 按紧急度排序
    results.sort(key=lambda h: (
        0 if h.urgency == "critical" else 1 if h.urgency == "warning" else 2,
        -h.idle_chapters,
    ))
    return results


def build_plot_context(health_results: list[PlotThreadHealth], max_plots: int = 10) -> str:
    """生成注入写作提示的伏笔摘要文本。

    只显示最紧急和最活跃的伏笔（最多 max_plots 个）。
    """
    if not health_results:
        return ""

    urgent = [h for h in health_results if h.urgency in ("critical", "warning")]
    normal = [h for h in health_results if h.urgency == "normal"]

    # 紧急的优先显示，然后按活跃度
    display = urgent[:max_plots] + normal[:max_plots - len(urgent)]
    display = display[:max_plots]

    lines = ["## 当前活跃伏笔（本章应推进或回收）"]
    lines.append("| 状态 | ID | 描述 | 已闲置 |")
    lines.append("|------|-----|------|--------|")

    for h in display:
        urgency_mark = {
            "critical": "🔴",
            "warning": "🟡",
            "normal": "  ",
        }.get(h.urgency, "  ")
        lines.append(
            f"| {urgency_mark} | {h.plot_id} | {h.description[:40]} | "
            f"{h.idle_chapters}章 |"
        )

    # 活跃伏笔总量
    active_count = len(health_results)
    lines.append(f"\n**活跃伏笔总数: {active_count}** (上限 {MAX_ACTIVE_PLOTS})")

    # 接近上限 → 禁止新增
    if active_count >= MAX_ACTIVE_PLOTS:
        lines.append(f"🚫 **伏笔池已满！本章禁止埋新伏笔，必须回收至少 1 个已有伏笔。**")
    elif active_count >= PLOTS_WARNING_THRESHOLD:
        lines.append(f"⚠️ 伏笔池接近上限，建议优先回收已有伏笔，本章最多埋 1 个新伏笔。")

    # 积压警告
    criticals = [h for h in health_results if h.urgency == "critical"]
    if criticals:
        lines.append(f"🔴 {len(criticals)} 个伏笔严重积压（>15章未推进），请优先处理！")

    return "\n".join(lines)


def save_plot_pool_json(json_path: Path, threads: list[dict]):
    """保存伏笔池到 JSON 文件。"""
    from datetime import datetime
    data = {
        "threads": threads,
        "_updated": datetime.now().isoformat(),
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
