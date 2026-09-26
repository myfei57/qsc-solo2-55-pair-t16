"""追日策略。

策略只回答"这一轮镜场该处于哪种模式"：聚焦、待机（离焦等待）还是收起。
聚焦有两个硬前提——辐照度已落盘且未过期、没有未复位的故障闩锁。任何一条不
满足都退化到待机或收起，绝不带着过期基线追日。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import FieldLimits

FOCUS = "focus"
STANDBY = "standby"
STOW = "stow"
NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class StrategyDecision:
    """一次策略判定结果。"""

    mode: str
    reason: str
    dni_w_m2: float

    @property
    def focuses(self) -> bool:
        return self.mode == FOCUS

    def as_dict(self) -> dict:
        return {"mode": self.mode, "reason": self.reason, "dni_w_m2": round(self.dni_w_m2, 2)}


class FieldStrategy:
    """按辐照度、落盘状态与闩锁状态给出镜场模式。"""

    def __init__(self, limits: FieldLimits) -> None:
        self._limits = limits
        self._evaluations = 0

    @property
    def limits(self) -> FieldLimits:
        return self._limits

    def decide(
        self,
        *,
        dni_w_m2: float,
        sun_above_horizon: bool,
        latched: bool,
        durable_ok: bool,
    ) -> StrategyDecision:
        """给出本轮的镜场模式。"""

        self._evaluations += 1
        dni = float(dni_w_m2)
        if latched:
            return StrategyDecision(STOW, "fault_latched", dni)
        if not sun_above_horizon:
            return StrategyDecision(STOW, "night", dni)
        if not durable_ok:
            return StrategyDecision(STANDBY, "irradiance_not_durable", dni)
        if dni < self._limits.focus_threshold_w_m2:
            return StrategyDecision(STANDBY, "below_focus_threshold", dni)
        return StrategyDecision(FOCUS, "durable_irradiance", dni)

    def evaluations(self) -> int:
        """返回累计判定次数。"""

        return self._evaluations
