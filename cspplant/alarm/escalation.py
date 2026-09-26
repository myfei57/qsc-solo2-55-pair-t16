"""告警升级。

同一条告警在升级窗口内反复出现就不再只是"记录一下"：次数越多等级越高，达到
最高级要通知值班长。升级只按"窗口内次数 + 严重度"计算，不引入时间衰减，避免
同一批数据在不同时刻算出不同等级。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import AlarmLimits
from .rules import ALARM, WARNING

LEVEL_NAMES = ("正常", "关注", "重要", "紧急")


@dataclass(frozen=True)
class Escalation:
    """一次升级判定结果。"""

    level: int
    name: str
    occurrences: int
    severity: str

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "name": self.name,
            "occurrences": self.occurrences,
            "severity": self.severity,
        }


class EscalationPolicy:
    """按窗口内出现次数与严重度定级。"""

    def __init__(self, limits: AlarmLimits) -> None:
        self._limits = limits

    @property
    def window_s(self) -> float:
        return self._limits.escalation_window_s

    def level_of(self, occurrences: int, severity: str) -> int:
        """给出等级（0–3）。"""

        count = max(0, int(occurrences))
        if count == 0:
            return 0
        level = 1 if severity == WARNING else 2
        if count >= 5:
            level += 1
        elif count >= 3 and severity == ALARM:
            level += 1
        return min(3, level)

    def evaluate(self, occurrences: int, severity: str) -> Escalation:
        """返回一次升级判定结果。"""

        level = self.level_of(occurrences, severity)
        return Escalation(level, LEVEL_NAMES[level], max(0, int(occurrences)), severity)
