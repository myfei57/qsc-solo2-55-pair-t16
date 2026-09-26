"""调度指令。

上层调度下发的是"要多少负荷"，但设备侧还有自己的保护上限（低流量、超温、
闩锁）。真实执行值取两者中更保守的那个：调度不能把本地保护降额顶回去。
``step`` 按斜率把执行值推向目标，避免负荷阶跃把吸热器打热。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import FieldLimits


@dataclass(frozen=True)
class DispatchOutcome:
    """一次指令求值结果。"""

    requested_pct: float
    ceiling_pct: float
    effective_pct: float
    clamped: bool
    writer: str

    def as_dict(self) -> dict:
        return {
            "requested_pct": round(self.requested_pct, 3),
            "ceiling_pct": round(self.ceiling_pct, 3),
            "effective_pct": round(self.effective_pct, 3),
            "clamped": self.clamped,
            "writer": self.writer,
        }


class DispatchController:
    """调度目标值、本地保护上限与斜率限幅。"""

    def __init__(self, limits: FieldLimits) -> None:
        self._limits = limits
        self._requested = 0.0
        self._target = 0.0
        self._current = 0.0
        self._writer = "none"
        self._clamps = 0
        self._resolves = 0

    @property
    def limits(self) -> FieldLimits:
        return self._limits

    def request(self, pct: float, writer: str) -> float:
        """登记一路调度目标值（夹到 0–100）。"""

        value = max(0.0, min(100.0, float(pct)))
        self._requested = value
        self._writer = str(writer)
        return value

    def resolve(self, ceiling_pct: float) -> DispatchOutcome:
        """把调度目标值与本地保护上限合成唯一执行目标。"""

        ceiling = max(0.0, min(100.0, float(ceiling_pct)))
        effective = min(self._requested, ceiling)
        clamped = effective < self._requested - 1e-9
        if clamped:
            self._clamps += 1
        self._target = effective
        self._resolves += 1
        return DispatchOutcome(self._requested, ceiling, effective, clamped, self._writer)

    def step(self, dt: float) -> float:
        """按斜率把执行值推向目标，返回推进后的执行值。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        delta = self._limits.dispatch_rate_pct_s * dt
        gap = self._target - self._current
        if abs(gap) <= delta:
            self._current = self._target
        else:
            self._current += delta if gap > 0 else -delta
        return self._current

    def requested(self) -> float:
        """返回最近一次登记的目标值。"""

        return self._requested

    def target(self) -> float:
        """返回最近一次求值得到的执行目标。"""

        return self._target

    def current(self) -> float:
        """返回当前执行值。"""

        return self._current

    def writer(self) -> str:
        """返回最近一次下发者。"""

        return self._writer

    def clamps(self) -> int:
        """返回被本地保护上限压住的次数。"""

        return self._clamps

    def resolves(self) -> int:
        """返回指令求值次数。"""

        return self._resolves
