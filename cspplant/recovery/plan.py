"""恢复计划：续做还是退回，按阶段判定（粒度定死为阶段）。

判定粒度是**阶段**而不是步骤：每个阶段整体判定为 重做 / 跳过 / 续做 / 待做 /
回退；阶段内部不做步骤级作废，步骤记录只用于续做时的幂等跳过。迁移阶段
（参数核对）在恢复时一律重做——停机期间可能有人改过参数，核对不过就把已
完成的阶段按相反顺序退干净。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import checkpoint as ck
from .checkpoint import RunCheckpoint

# 阶段判定。
REVERIFY = "reverify"
SKIP = "skip"
RESUME = "resume"
AWAIT = "await"
ROLLBACK = "rollback"

# 计划结论。
OUTCOME_NOOP = "noop"
OUTCOME_RESUME = "resume"
OUTCOME_ROLLBACK = "rollback"
OUTCOME_COMPLETE = "complete"


@dataclass
class PhaseDecision:
    """一个阶段的判定。"""

    phase: str
    decision: str
    reason: str

    def as_dict(self) -> dict:
        return {"phase": self.phase, "decision": self.decision, "reason": self.reason}


@dataclass
class RecoveryPlan:
    """一次恢复的完整判定。"""

    run_id: str
    outcome: str
    consistent: bool
    changed: list[dict] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    decisions: list[PhaseDecision] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "outcome": self.outcome,
            "consistent": self.consistent,
            "changed": self.changed,
            "checks": self.checks,
            "decisions": [decision.as_dict() for decision in self.decisions],
        }


def build_plan(
    checkpoint: RunCheckpoint | None,
    changed: list[dict],
    checks: list[dict],
    phase_order: tuple[str, ...],
) -> RecoveryPlan:
    """按检查点、参数差异与核对结果给出逐阶段判定（纯函数，不碰现场）。"""

    if checkpoint is None or checkpoint.state not in ck.RECOVERABLE:
        return RecoveryPlan("", OUTCOME_NOOP, consistent=not changed)

    decisions = [PhaseDecision(phase_order[0], REVERIFY, "参数核对属于迁移，恢复时一律重做")]
    checks_ok = all(item["ok"] for item in checks)

    if not checks_ok:
        for name in phase_order[1:]:
            record = checkpoint.phase(name)
            if record.has_effects():
                decisions.append(PhaseDecision(name, ROLLBACK, "参数核对未通过，按相反顺序退干净"))
            else:
                decisions.append(PhaseDecision(name, AWAIT, "未产生现场效果，无需回退"))
        return RecoveryPlan(
            checkpoint.run_id, OUTCOME_ROLLBACK, not changed, list(changed), list(checks), decisions
        )

    resumable = False
    for name in phase_order[1:]:
        record = checkpoint.phase(name)
        if record.state == ck.DONE:
            decisions.append(PhaseDecision(name, SKIP, "阶段已完成，幂等跳过"))
        elif not resumable:
            decisions.append(PhaseDecision(name, RESUME, "首个未完成阶段，从此续做"))
            resumable = True
        else:
            decisions.append(PhaseDecision(name, AWAIT, "排在前序阶段之后，依次执行"))
    outcome = OUTCOME_RESUME if resumable else OUTCOME_COMPLETE
    return RecoveryPlan(checkpoint.run_id, outcome, not changed, list(changed), list(checks), decisions)
