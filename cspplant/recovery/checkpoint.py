"""启动运行的持久化检查点。

每完成一步就把进度原子落盘（``JsonStore`` 先写临时文件再 ``os.replace``），
掉电也不会留下半截检查点。重启后按检查点判断哪些阶段已完成、哪个阶段被打
断，不需要现场人工逐步核对。检查点只记"做到哪一步"，现场效果由执行器在续做
时重新核对，两者口径一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..store.jsonfile import JsonStore

CHECKPOINT_DOC = "recovery/startup"
LAST_REPORT_DOC = "recovery/last_report"

# 阶段与步骤状态。
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
ROLLED_BACK = "rolled_back"

# 运行状态。
RUN_ACTIVE = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"
RUN_ROLLED_BACK = "rolled_back"

# 处于这些状态的运行需要恢复（续做或回退），不允许直接开新运行。
RECOVERABLE = (RUN_ACTIVE, RUN_FAILED)


@dataclass
class StepRecord:
    """一个步骤的进度记录。"""

    name: str
    state: str = PENDING
    ts: float = 0.0
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "ts": round(self.ts, 3),
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "StepRecord | None":
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            return None
        detail = raw.get("detail")
        return cls(
            name=raw["name"],
            state=str(raw.get("state", PENDING)),
            ts=float(raw.get("ts", 0.0)),
            detail=dict(detail) if isinstance(detail, dict) else {},
        )


@dataclass
class PhaseRecord:
    """一个阶段的进度记录。"""

    name: str
    state: str = PENDING
    updated_ts: float = 0.0
    steps: list[StepRecord] = field(default_factory=list)

    def step(self, name: str) -> StepRecord | None:
        """按名字取步骤记录。"""

        for record in self.steps:
            if record.name == name:
                return record
        return None

    def mark_step(self, name: str, state: str, ts: float, detail: dict) -> StepRecord:
        """写入（或更新）一个步骤的进度。"""

        record = self.step(name)
        if record is None:
            record = StepRecord(name)
            self.steps.append(record)
        record.state = state
        record.ts = float(ts)
        record.detail = dict(detail)
        return record

    def has_effects(self) -> bool:
        """该阶段是否已产生现场效果（回退时需要处理）。"""

        if self.state in (RUNNING, FAILED, DONE):
            return True
        return any(step.state == DONE for step in self.steps)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "updated_ts": round(self.updated_ts, 3),
            "steps": [step.as_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PhaseRecord | None":
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            return None
        steps = [StepRecord.from_dict(item) for item in raw.get("steps", [])]
        return cls(
            name=raw["name"],
            state=str(raw.get("state", PENDING)),
            updated_ts=float(raw.get("updated_ts", 0.0)),
            steps=[step for step in steps if step is not None],
        )


@dataclass
class RunCheckpoint:
    """一次启动运行的完整进度。"""

    run_id: str
    operation: str
    state: str
    created_ts: float
    updated_ts: float
    fingerprint: str
    snapshot: dict
    phases: list[PhaseRecord]

    @classmethod
    def new(
        cls,
        run_id: str,
        operation: str,
        snapshot: dict,
        fingerprint: str,
        now: float,
        phase_order: tuple[str, ...],
    ) -> "RunCheckpoint":
        """开一份新运行的检查点，全部阶段置为待做。"""

        return cls(
            run_id=run_id,
            operation=operation,
            state=RUN_ACTIVE,
            created_ts=float(now),
            updated_ts=float(now),
            fingerprint=fingerprint,
            snapshot=snapshot,
            phases=[PhaseRecord(name) for name in phase_order],
        )

    def phase(self, name: str) -> PhaseRecord:
        """按名字取阶段记录。"""

        for record in self.phases:
            if record.name == name:
                return record
        raise KeyError(f"检查点里没有阶段 {name}")

    def reset_phase(self, name: str, now: float) -> PhaseRecord:
        """把一个阶段清回待做（迁移阶段恢复时重做用）。"""

        record = self.phase(name)
        record.state = PENDING
        record.updated_ts = float(now)
        record.steps = []
        return record

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "operation": self.operation,
            "state": self.state,
            "created_ts": round(self.created_ts, 3),
            "updated_ts": round(self.updated_ts, 3),
            "fingerprint": self.fingerprint,
            "snapshot": self.snapshot,
            "phases": [phase.as_dict() for phase in self.phases],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RunCheckpoint | None":
        """从落盘文档还原；结构不完整时返回 ``None``，由调用方当作无检查点。"""

        if not isinstance(raw, dict):
            return None
        run_id = raw.get("run_id")
        snapshot = raw.get("snapshot")
        phases = raw.get("phases")
        if not isinstance(run_id, str) or not isinstance(snapshot, dict) or not isinstance(phases, list):
            return None
        restored = [PhaseRecord.from_dict(item) for item in phases]
        if any(record is None for record in restored):
            return None
        return cls(
            run_id=run_id,
            operation=str(raw.get("operation", "startup")),
            state=str(raw.get("state", RUN_ACTIVE)),
            created_ts=float(raw.get("created_ts", 0.0)),
            updated_ts=float(raw.get("updated_ts", 0.0)),
            fingerprint=str(raw.get("fingerprint", "")),
            snapshot=snapshot,
            phases=[record for record in restored if record is not None],
        )


class CheckpointStore:
    """检查点与最近一次恢复报告的读写。"""

    def __init__(self, store: JsonStore) -> None:
        self._store = store

    def load(self) -> RunCheckpoint | None:
        """读回检查点；没有或损坏时返回 ``None``。"""

        return RunCheckpoint.from_dict(self._store.read(CHECKPOINT_DOC))

    def save(self, checkpoint: RunCheckpoint) -> str:
        """原子落盘检查点。"""

        return self._store.write(CHECKPOINT_DOC, checkpoint.as_dict())

    def clear(self) -> bool:
        """清掉检查点（交付前重置用）。"""

        return self._store.remove(CHECKPOINT_DOC)

    def save_report(self, report: dict) -> str:
        """落盘最近一次启动／恢复报告，供控制台跨重启展示。"""

        return self._store.write(LAST_REPORT_DOC, report)

    def load_report(self) -> dict | None:
        """读回最近一次报告。"""

        raw = self._store.read(LAST_REPORT_DOC)
        return dict(raw) if isinstance(raw, dict) else None
