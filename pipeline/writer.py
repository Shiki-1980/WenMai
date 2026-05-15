"""Vault 写入层 —— 将生成内容写回 Obsidian vault。"""

import json
from datetime import datetime
from pathlib import Path

import frontmatter
import yaml


def _write_frontmatter_md(path: Path, metadata: dict, body: str):
    """写入带 frontmatter 的 markdown 文件。"""
    clean = {}
    for k, v in metadata.items():
        if v is not None and v != "" and v != [] and v != {}:
            clean[k] = v

    content = "---\n"
    content += yaml.dump(clean, allow_unicode=True, default_flow_style=False, sort_keys=False, width=200)
    content += "---\n\n"
    content += body

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, "utf-8")


class VaultWriter:
    TYPE_DIR = {
        "person": "person",
        "item": "item",
        "location": "location",
        "concept": "concept",
    }

    def __init__(self, vault_path: str, template_dir: str = ""):
        self.root = Path(vault_path)
        self.entity_dir = self.root / "entity"
        self.template_dir = Path(template_dir) if template_dir else self.root / "_templates"

    # ---- 章节 ----

    def write_chapter(self, number: int, title: str, body: str):
        """写入章节正文。"""
        path = self.root / "chapter" / f"ch_{number:03d}.md"
        _write_frontmatter_md(
            path,
            {
                "chapter": number,
                "title": title,
                "created": datetime.now().strftime("%Y-%m-%d"),
            },
            body,
        )
        return path

    # ---- 章节摘要 ----

    def write_summary(self, number: int, meta: dict, body: str):
        """写入章节摘要（含实体频率、RAG 链接）。"""
        path = self.root / "summary" / f"ch_{number:03d}_summary.md"
        _write_frontmatter_md(path, meta, body)
        return path

    # ---- 实体卡 ----

    def update_entity(self, entity_type: str, name: str, updates: dict):
        """
        更新实体卡的 frontmatter 字段。
        updates: {"field": "new_value", ...}
        """
        subdir = self.TYPE_DIR.get(entity_type, "person")
        path = self.entity_dir / subdir / f"{name}.md"

        if path.exists():
            post = frontmatter.load(str(path))
            meta = dict(post.metadata)
            body = post.content
        else:
            meta = {"type": entity_type}
            body = f"# {name}\n\n"

        for field, value in updates.items():
            if value:
                meta[field] = value

        meta["updated"] = datetime.now().strftime("%Y-%m-%d")
        _write_frontmatter_md(path, meta, body)

    def create_entity(self, entity_type: str, name: str, brief: str = ""):
        """创建新实体卡，基于 _templates/ 下的模板。"""
        subdir = self.TYPE_DIR.get(entity_type, "person")
        path = self.entity_dir / subdir / f"{name}.md"
        if path.exists():
            return

        # 读取对应模板
        template_path = self.template_dir / f"{entity_type}.md"
        if template_path.exists():
            template_body = template_path.read_text("utf-8")
            # 替换模板变量
            today = datetime.now().strftime("%Y-%m-%d")
            now = datetime.now().strftime("%H:%M")
            body = (template_body
                    .replace("{{title}}", name)
                    .replace("{{date}}", today)
                    .replace("{{time}}", now))
            # 提取模板的 frontmatter 作为基础 meta
            try:
                post = frontmatter.load(str(template_path))
                meta = dict(post.metadata)
            except Exception:
                meta = {}
        else:
            body = f"# {name}\n\n## 描述\n{brief}\n"
            meta = {}

        meta["type"] = entity_type
        meta["status"] = "active"
        meta["created"] = datetime.now().strftime("%Y-%m-%d")
        meta["updated"] = datetime.now().strftime("%Y-%m-%d")

        if brief:
            body += f"\n<!-- 蒸馏摘要 -->\n{brief}\n"

        _write_frontmatter_md(path, meta, body)

    def update_entity_field(self, entity_type: str, name: str, field: str, new_value: str):
        """更新实体卡单个字段（用于蒸馏后的自动更新）。"""
        self.update_entity(entity_type, name, {field: new_value})

    def append_entity_timeline(self, entity_type: str, name: str, chapter: int, event: str):
        """在实体卡的「经历时间线」追加一行。"""
        subdir = self.TYPE_DIR.get(entity_type, "person")
        path = self.entity_dir / subdir / f"{name}.md"
        if not path.exists():
            return

        post = frontmatter.load(str(path))
        body = post.content

        # 在时间线表格后添加行
        timeline_marker = "| 章节 | 事件摘要 |"
        if timeline_marker in body:
            body += f"| ch_{chapter:03d} | {event} |\n"

        _write_frontmatter_md(path, dict(post.metadata), body)

    # ---- 索引 ----

    def update_entity_index(self, index_updates: dict[str, list[int]]):
        """更新实体→章节倒排索引。"""
        idx_path = self.root / "index" / "entity_chapter_index.json"

        if idx_path.exists():
            idx = json.loads(idx_path.read_text("utf-8"))
        else:
            idx = {"_description": "实体→章节倒排索引", "_updated": "", "entities": {}}

        for name, chapters in index_updates.items():
            idx["entities"][name] = {
                "chapters": sorted(set(chapters)),
            }

        idx["_updated"] = datetime.now().isoformat()
        idx_path.write_text(
            json.dumps(idx, ensure_ascii=False, indent=2), "utf-8"
        )

    # ---- 伏笔池 ----

    def _ensure_plot_pool(self) -> Path:
        """确保伏笔池文件存在，不存在则创建空模板。"""
        path = self.root / "plot" / "伏笔池.md"
        if not path.exists():
            path.write_text(
                "# 伏笔池\n\n"
                "## 进行中的伏笔\n\n"
                "| ID | 描述 | 埋下章节 | 涉及实体 | 预计回收 | 状态 |\n"
                "|----|------|----------|----------|----------|------|\n\n"
                "## 已回收的伏笔\n\n"
                "| ID | 描述 | 埋下章节 | 回收章节 | 涉及实体 | 状态 |\n"
                "|----|------|----------|----------|----------|------|\n",
                "utf-8",
            )
        return path

    def add_plot_thread(self, plot_desc: str, chapter_number: int):
        """在伏笔池中添加新伏笔（进行中）。"""
        path = self._ensure_plot_pool()
        content = path.read_text("utf-8")

        # 生成唯一 ID
        existing_ids = set()
        import re as _re
        for m in _re.finditer(r"\| (P\d+) \|", content):
            existing_ids.add(m.group(1))
        pid = f"P{chapter_number:03d}"
        counter = 1
        while pid in existing_ids:
            pid = f"P{chapter_number:03d}_{counter}"
            counter += 1

        line = f"| {pid} | {plot_desc} | ch_{chapter_number:03d} | | | 进行中 |\n"

        marker = "## 已回收的伏笔"
        idx = content.find(marker)
        if idx == -1:
            content += "\n" + line
        else:
            content = content[:idx] + line + "\n" + content[idx:]

        path.write_text(content, "utf-8")
        return pid

    def reveal_plot_thread(self, plot_desc: str, chapter_number: int):
        """将伏笔标记为已回收，移至回收区。"""
        path = self.root / "plot" / "伏笔池.md"
        if not path.exists():
            return
        content = path.read_text("utf-8")

        # 在进行中的伏笔区域查找匹配行
        lines = content.split("\n")
        new_active = []
        revealed_line = None
        in_active_section = False
        in_revealed_section = False
        revealed_lines = []

        for line in lines:
            if line.startswith("## 进行中的伏笔"):
                in_active_section = True
                new_active.append(line)
                continue
            if line.startswith("## 已回收的伏笔"):
                in_active_section = False
                in_revealed_section = True
                new_active.append(line)
                continue
            if in_revealed_section:
                revealed_lines.append(line)
                continue

            if in_active_section and line.startswith("|") and plot_desc in line:
                # 找到匹配伏笔：修改状态为已回收，记录回收章节
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) >= 6:
                    cells[4] = f"ch_{chapter_number:03d}"  # 预计回收 -> 回收章节
                    cells[5] = "已回收"
                    revealed_line = "| " + " | ".join(cells) + " |"
                continue  # 从进行中区域移除
            new_active.append(line)

        if revealed_line:
            # 重建文件
            result = "\n".join(new_active)
            if "## 已回收的伏笔" in result:
                # 在回收表头后插入
                marker = "|----|------|----------|----------|----------|------|"
                ridx = result.find(marker)
                if ridx != -1:
                    after_header = result.find("\n", ridx) + 1
                    result = result[:after_header] + revealed_line + "\n" + result[after_header:]
            else:
                result += "\n" + revealed_line + "\n"

            path.write_text(result, "utf-8")
            print(f"  -> 伏笔已回收: {plot_desc[:40]}...")
