"""故障闩锁。

越限之后必须由人确认并复位，温度回落不能自己清闩锁。复位还有冷却时间：闩锁
刚跳的几秒内不允许复位，避免"抖一下就复位"把保护绕过去。闩锁记录跳闸来源与
原因，供审计与告警页展示。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import AlarmLimits
from ..errors import InterlockError


@dataclass(frozen=True)
class LatchState:
    """闩锁状态。"""

    latched: bool
    source: str
    reason: str
    tripped_at: float
    resets: int

    def as_dict(self) -> dict:
        return {
            "latched": self.latched,
            "source": self.source,
            "reason": self.reason,
            "tripped_at": round(self.tripped_at, 3),
            "resets": self.resets,
        }


class FaultLatch:
    """带冷却时间的故障闩锁。"""

    def __init__(self, limits: AlarmLimits) -> None:
        self._limits = limits
        self._latched = False
        self._source = ""
        self._reason = ""
        self._tripped_at = 0.0
        self._trips = 0
        self._resets = 0
        self._rejections = 0

    @property
    def limits(self) -> AlarmLimits:
        return self._limits

    def trip(self, source: str, reason: str, now: float) -> LatchState:
        """置闩锁；重复置位会刷新原因与时刻。"""

        if not source or not reason:
            raise InterlockError("闩锁必须记录来源与原因", component="alarm")
        self._latched = True
        self._source = str(source)
        self._reason = str(reason)
        self._tripped_at = float(now)
        self._trips += 1
        return self.state()

    def latched(self) -> bool:
        """闩锁是否处于置位状态。"""

        return self._latched

    def ready_to_reset(self, now: float) -> bool:
        """冷却时间是否已过。"""

        if not self._latched:
            return False
        return (float(now) - self._tripped_at) >= self._limits.latch_reset_delay_s

    def reset(self, now: float, operator: str) -> LatchState:
        """复位闩锁；冷却时间未到即拒绝。"""

        if not self._latched:
            raise InterlockError("故障闩锁未置位，无需复位", component="alarm")
        if not self.ready_to_reset(now):
            self._rejections += 1
            remaining = self._limits.latch_reset_delay_s - (float(now) - self._tripped_at)
            raise InterlockError(f"闩锁冷却中，还需 {remaining:.1f} 秒", component="alarm")
        if not operator:
            raise InterlockError("复位必须记录操作人", component="alarm")
        self._latched = False
        self._resets += 1
        return self.state()

    def source(self) -> str:
        """返回跳闸来源。"""

        return self._source

    def reason(self) -> str:
        """返回跳闸原因。"""

        return self._reason

    def tripped_at(self) -> float:
        """返回置位时刻。"""

        return self._tripped_at

    def trips(self) -> int:
        """返回累计置位次数。"""

        return self._trips

    def resets(self) -> int:
        """返回累计复位次数。"""

        return self._resets

    def rejections(self) -> int:
        """返回累计被拒的复位次数。"""

        return self._rejections

    def state(self) -> LatchState:
        """返回闩锁状态快照。"""

        return LatchState(self._latched, self._source, self._reason, self._tripped_at, self._resets)
