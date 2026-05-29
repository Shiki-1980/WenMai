"""章节提交记录 —— 不可变的状态变更历史。

每次蒸馏 + state 更新完成后，保存一条不可变提交到:
  novels/<name>/commits/chapter_NNN.commit.json

提交一旦写入就不再修改，提供完整的变更审计轨迹。
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class FactChange:
    """一条事实变更。"""
    predicate: str
    object: str
    old_value: str = ""
    evidence: str = ""


@dataclass
class EntityDeltaRecord:
    """一个实体的 delta 记录（提交用）。"""
    entity: str
    entity_type: str
    facts_added: list[FactChange] = field(default_factory=list)
    facts_retired: list[dict] = field(default_factory=list)  # {predicate, object}


@dataclass
class DisambigRecord:
    """消歧记录。"""
    candidate: str
    resolved_to: str | None = None
    confidence: float = 0.0
    action: str = "pending"


@dataclass
class ChapterCommit:
    """一次章节提交的完整记录。"""
    chapter: int
    timestamp: str = ""
    schema_snapshot_hash: str = ""
    entity_deltas_applied: list[EntityDeltaRecord] = field(default_factory=list)
    new_entities_created: list[str] = field(default_factory=list)
    disambiguations: list[DisambigRecord] = field(default_factory=list)
    plots_added: list[str] = field(default_factory=list)
    plots_resolved: list[str] = field(default_factory=list)
    retrieval_stats: dict = field(default_factory=dict)
    state_snapshot_before_hash: str = ""
    state_snapshot_after_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "chapter": self.chapter,
            "timestamp": self.timestamp,
            "schema_snapshot_hash": self.schema_snapshot_hash,
            "entity_deltas_applied": [
                {
                    "entity": d.entity,
                    "entity_type": d.entity_type,
                    "facts_added": [asdict(f) for f in d.facts_added],
                    "facts_retired": d.facts_retired,
                }
                for d in self.entity_deltas_applied
            ],
            "new_entities_created": self.new_entities_created,
            "disambiguations": [asdict(d) for d in self.disambiguations],
            "plots_added": self.plots_added,
            "plots_resolved": self.plots_resolved,
            "retrieval_stats": self.retrieval_stats,
            "state_snapshot_before_hash": self.state_snapshot_before_hash,
            "state_snapshot_after_hash": self.state_snapshot_after_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChapterCommit":
        return cls(
            chapter=data["chapter"],
            timestamp=data.get("timestamp", ""),
            schema_snapshot_hash=data.get("schema_snapshot_hash", ""),
            entity_deltas_applied=[
                EntityDeltaRecord(
                    entity=d["entity"],
                    entity_type=d.get("entity_type", "person"),
                    facts_added=[FactChange(**f) for f in d.get("facts_added", [])],
                    facts_retired=d.get("facts_retired", []),
                )
                for d in data.get("entity_deltas_applied", [])
            ],
            new_entities_created=data.get("new_entities_created", []),
            disambiguations=[DisambigRecord(**d) for d in data.get("disambiguations", [])],
            plots_added=data.get("plots_added", []),
            plots_resolved=data.get("plots_resolved", []),
            retrieval_stats=data.get("retrieval_stats", {}),
            state_snapshot_before_hash=data.get("state_snapshot_before_hash", ""),
            state_snapshot_after_hash=data.get("state_snapshot_after_hash", ""),
        )


class CommitStore:
    """章节提交记录管理器。"""

    def __init__(self, novel_dir: Path):
        self.commits_dir = novel_dir / "commits"
        self.commits_dir.mkdir(parents=True, exist_ok=True)
        self._schema_path = novel_dir / "novel_schema.json"

    def _commit_path(self, chapter: int) -> Path:
        return self.commits_dir / f"chapter_{chapter:03d}.commit.json"

    def save_commit(self, commit: ChapterCommit):
        """保存一条提交（不可变——不提供 update 接口）。"""
        path = self._commit_path(commit.chapter)
        if path.exists():
            print(f"  [WARN] 第 {commit.chapter} 章的 commit 已存在，跳过（不可覆盖）")
            return

        commit.timestamp = datetime.now().isoformat()
        commit.schema_snapshot_hash = self._hash_schema_snapshot()
        path.write_text(
            json.dumps(commit.to_dict(), ensure_ascii=False, indent=2),
            "utf-8",
        )

    def load_commit(self, chapter: int) -> ChapterCommit | None:
        """加载一条提交。"""
        path = self._commit_path(chapter)
        if not path.exists():
            return None
        data = json.loads(path.read_text("utf-8"))
        return ChapterCommit.from_dict(data)

    def list_commits(self) -> list[int]:
        """列出所有已提交的章节号。"""
        commits = []
        for p in sorted(self.commits_dir.glob("chapter_*.commit.json")):
            try:
                num = int(p.stem.replace("chapter_", "").replace(".commit", ""))
                commits.append(num)
            except ValueError:
                pass
        return commits

    def commit_count(self) -> int:
        return len(self.list_commits())

    def _hash_schema_snapshot(self) -> str:
        """计算当前 schema 文件的 hash。"""
        if not self._schema_path.exists():
            return "no_schema"
        content = self._schema_path.read_bytes()
        return hashlib.sha256(content).hexdigest()[:16]


def build_commit_from_writer(
    chapter: int,
    entity_deltas: list[dict],
    new_entities: list[str],
    disambiguations: list[DisambigRecord],
    plots_added: list[str],
    plots_resolved: list[str],
    retrieval_stats: dict | None = None,
    state_snapshot_before_hash: str = "",
    state_snapshot_after_hash: str = "",
) -> ChapterCommit:
    """从 writer 的输出构建 ChapterCommit。"""
    records = []
    for delta in entity_deltas:
        facts_added = [
            FactChange(
                predicate=f.get("predicate", ""),
                object=f.get("object", ""),
                old_value=f.get("old_value", ""),
                evidence=f.get("evidence", ""),
            )
            for f in delta.get("facts", [])
        ]
        records.append(EntityDeltaRecord(
            entity=delta.get("entity", ""),
            entity_type=delta.get("entity_type", "person"),
            facts_added=facts_added,
        ))

    return ChapterCommit(
        chapter=chapter,
        entity_deltas_applied=records,
        new_entities_created=new_entities,
        disambiguations=disambiguations,
        plots_added=plots_added,
        plots_resolved=plots_resolved,
        retrieval_stats=retrieval_stats or {},
        state_snapshot_before_hash=state_snapshot_before_hash,
        state_snapshot_after_hash=state_snapshot_after_hash,
    )


# ═══════════════════════════════════════════════════════════════
# State snapshot utilities
# ═══════════════════════════════════════════════════════════════

def hash_directory(dir_path: Path) -> str:
    """计算 state 目录的 SHA256 hash（用于 commit 校验）。"""
    if not dir_path.exists():
        return "no_state"
    hasher = hashlib.sha256()
    for f in sorted(dir_path.rglob("*.json")):
        hasher.update(f.read_bytes())
    return hasher.hexdigest()[:16]


def snapshot_state(state_dir: Path, snapshot_dir: Path, chapter: int) -> str:
    """将当前 state/ 目录完整复制到 snapshots/ch_NNN/。

    在每次章节写入前调用，保存写入前的状态快照。
    返回快照目录的 hash。

    如果快照已存在（重复调用），跳过并返回已有 hash。
    """
    import shutil
    target = snapshot_dir / f"ch_{chapter:03d}"
    if target.exists():
        return hash_directory(target)

    if not state_dir.exists():
        return "no_state"

    # 复制 state/ 目录到 snapshots/ch_NNN/
    shutil.copytree(state_dir, target)
    return hash_directory(target)


# ═══════════════════════════════════════════════════════════════
# State reconstruction from commits (snapshot fallback)
# ═══════════════════════════════════════════════════════════════

def reconstruct_state_from_commits(novel_dir: Path, target_chapter: int) -> dict:
    """从 commit 日志重建 target_chapter 之前的状态（快照不可用时的兜底方案）。

    按章节顺序重放所有 commit，逐章 apply delta，重建完整的 EntityState 字典。

    Args:
        target_chapter: 重建到这个章节之前的状态（不包含 target_chapter 的变更）

    Returns:
        {entity_key: EntityState} 字典，key 格式为 "entity_type/entity_name"
    """
    from state_schema import EntityState, EntityFact

    store = CommitStore(novel_dir / "commits" if (novel_dir / "commits").exists() else novel_dir)
    # Handle the case where novel_dir already contains "commits" subpath
    if not (novel_dir / "commits").exists():
        store = CommitStore(novel_dir)
    else:
        store = CommitStore(novel_dir)

    all_commits = store.list_commits()
    commits = [c for c in all_commits if c < target_chapter]
    commits.sort()

    if not commits:
        return {}

    states: dict[str, EntityState] = {}

    for ch in commits:
        commit = store.load_commit(ch)
        if not commit:
            continue

        for delta_record in commit.entity_deltas_applied:
            key = f"{delta_record.entity_type}/{delta_record.entity}"

            # 懒初始化实体状态
            if key not in states:
                states[key] = EntityState(
                    entity=delta_record.entity,
                    entity_type=delta_record.entity_type,
                    last_updated_chapter=0,
                )

            state = states[key]

            # Apply facts_added: 标记旧事实为 retired，追加新事实
            for fact_change in delta_record.facts_added:
                for old_fact in state.facts:
                    if old_fact.predicate == fact_change.predicate and old_fact.until_chapter is None:
                        old_fact.until_chapter = ch

                state.facts.append(EntityFact(
                    predicate=fact_change.predicate,
                    object=fact_change.object,
                    since_chapter=ch,
                    source=f"ch_{ch:03d}蒸馏",
                    evidence=fact_change.evidence,
                ))

            # Apply facts_retired
            for retired in delta_record.facts_retired:
                pred = retired.get("predicate", "") if isinstance(retired, dict) else ""
                for old_fact in state.facts:
                    if old_fact.predicate == pred and old_fact.until_chapter is None:
                        old_fact.until_chapter = ch

            state.last_updated_chapter = ch

    return states


def write_reconstructed_state(states: dict, state_dir: Path):
    """将重建的状态字典写入 state/ 目录。"""
    import shutil as _shutil

    if state_dir.exists():
        _shutil.rmtree(state_dir)

    for key, state in states.items():
        etype, name = key.split("/", 1)
        path = state_dir / etype / f"{name}.state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            __import__('json').dumps(state.to_dict(), ensure_ascii=False, indent=2),
            "utf-8",
        )
