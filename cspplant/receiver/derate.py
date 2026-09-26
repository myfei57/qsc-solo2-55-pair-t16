"""降额控制。

出口温度进入降额带之后必须主动压低出力上限，而不是等到跳闸。这里带一个
回差：进入降额的温度阈值低于退出阈值，避免温度在阈值附近抖动时上限反复跳。
降额起点与上限都用出口温度线性插值，给出 100% → 0% 的连续上限曲线。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ReceiverLimits

# 退出降额需要低于进入阈值这么多度，防止在边界上来回切。
HYSTERESIS_C = 5.0


@dataclass(frozen=True)
class DerateState:
    """一次降额判定结果。"""

    active: bool
    ceiling_pct: float
    temperature_c: float
    changed: bool

    def as_dict(self) -> dict:
        return {
            "active": self.active,
            "ceiling_pct": round(self.ceiling_pct, 3),
            "temperature_c": round(self.temperature_c, 2),
            "changed": self.changed,
        }


class DerateController:
    """按出口温度给出出力上限。"""

    def __init__(self, limits: ReceiverLimits) -> None:
        self._limits = limits
        self._active = False
        self._entries = 0
        self._clears = 0
        self._evaluations = 0
        self._min_ceiling = 100.0
        self._last_temp = float(limits.temp_target_c)

    @property
    def limits(self) -> ReceiverLimits:
        return self._limits

    def ceiling_pct(self, temperature_c: float) -> float:
        """按出口温度算出力上限（不改变状态）。"""

        temp = float(temperature_c)
        if temp <= self._limits.temp_derate_c:
            return 100.0
        span = self._limits.temp_max_c - self._limits.temp_derate_c
        if span <= 0:
            return 0.0
        ratio = (self._limits.temp_max_c - temp) / span
        return max(0.0, min(100.0, 100.0 * ratio))

    def evaluate(self, temperature_c: float) -> DerateState:
        """推进一次判定，返回降额状态。"""

        self._evaluations += 1
        temp = float(temperature_c)
        self._last_temp = temp
        ceiling = self.ceiling_pct(temp)
        self._min_ceiling = min(self._min_ceiling, ceiling)
        changed = False
        if not self._active and temp >= self._limits.temp_derate_c:
            self._active = True
            self._entries += 1
            changed = True
        elif self._active and temp < self._limits.temp_derate_c - HYSTERESIS_C:
            self._active = False
            self._clears += 1
            changed = True
        return DerateState(self._active, ceiling if self._active else 100.0, temp, changed)

    def active(self) -> bool:
        """当前是否处于降额。"""

        return self._active

    def current_ceiling_pct(self) -> float:
        """按最近一次判定温度给出出力上限（未降额时为 100）。"""

        ceiling = self.ceiling_pct(self.last_temperature_c())
        return ceiling if self._active else 100.0

    def last_temperature_c(self) -> float:
        """返回最近一次参与判定的出口温度。"""

        return self._last_temp

    def entries(self) -> int:
        """返回进入降额的次数。"""

        return self._entries

    def clears(self) -> int:
        """返回退出降额的次数。"""

        return self._clears

    def evaluation_count(self) -> int:
        """返回累计判定次数。"""

        return self._evaluations

    def min_ceiling_pct(self) -> float:
        """返回历史最低出力上限。"""

        return self._min_ceiling
