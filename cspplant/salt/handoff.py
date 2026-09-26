"""冷热罐切换顺序。

切换只有一种正确顺序：先停冷泵、等它真正停稳，再启热泵。反过来做会让两台泵
同时压在母管上，压力瞬间顶到保护值，安全阀动作。这里把顺序写死在编排里，
每一步都推进到条件真正成立才继续，并把切换过程中的母管峰值记进报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import SaltLimits
from ..errors import InterlockError
from ..receiver.header import SaltHeader
from .hydraulic import HydraulicLoop
from .pump import SaltPump
from .tank import SaltTank

MAX_STEPS = 1800


@dataclass(frozen=True)
class HandoffReport:
    """一次切换的结果。"""

    ok: bool
    steps: list[dict] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    peak_pressure_mpa: float = 0.0
    max_pumps_running: int = 0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "steps": self.steps,
            "violations": self.violations,
            "peak_pressure_mpa": round(self.peak_pressure_mpa, 3),
            "max_pumps_running": self.max_pumps_running,
        }


class TankHandoff:
    """按「先停冷泵、再启热泵」的顺序执行切换。"""

    def __init__(self, limits: SaltLimits) -> None:
        self._limits = limits
        self._changeovers = 0

    @property
    def limits(self) -> SaltLimits:
        return self._limits

    def changeover(
        self,
        cold_pump: SaltPump,
        hot_pump: SaltPump,
        cold_tank: SaltTank,
        hot_tank: SaltTank,
        header: SaltHeader,
        hydraulic: HydraulicLoop,
        dt: float = 1.0,
    ) -> HandoffReport:
        """执行一次冷盐泵 → 热盐泵的切换。"""

        steps: list[dict] = []
        violations: list[str] = []
        peak = header.pressure_mpa()
        max_running = 0

        if hot_tank.headroom_kg() <= 0.0:
            violations.append("hot_tank_full")
            return HandoffReport(False, steps, violations, peak, max_running)
        if cold_tank.at_min():
            violations.append("cold_tank_empty")
            return HandoffReport(False, steps, violations, peak, max_running)
        if hot_pump.running():
            violations.append("hot_pump_already_running")
            return HandoffReport(False, steps, violations, peak, max_running)
        if not cold_pump.running():
            violations.append("cold_pump_not_running")
            return HandoffReport(False, steps, violations, peak, max_running)

        # 第一步：停冷泵，并推进到流量真正归零。
        cold_pump.stop()
        for _ in range(MAX_STEPS):
            flow = hydraulic.update([cold_pump, hot_pump], dt)
            pumps = sum(1 for pump in (cold_pump, hot_pump) if pump.running())
            max_running = max(max_running, pumps)
            peak = max(peak, header.update(flow, pumps, dt))
            if cold_pump.idle():
                break
        steps.append(
            {
                "component": "salt",
                "action": "stop_cold_pump",
                "detail": {"cold_flow_kg_s": round(cold_pump.flow_kg_s(), 3), "idle": cold_pump.idle()},
            }
        )
        if not cold_pump.idle():
            violations.append("cold_pump_not_stopped")
            return HandoffReport(False, steps, violations, peak, max_running)

        # 第二步：冷泵停稳之后才允许启热泵。
        hot_pump.start()
        steps.append(
            {
                "component": "salt",
                "action": "start_hot_pump",
                "detail": {"target_flow_kg_s": round(hot_pump.target_flow_kg_s(), 3)},
            }
        )
        for _ in range(MAX_STEPS):
            flow = hydraulic.update([cold_pump, hot_pump], dt)
            pumps = sum(1 for pump in (cold_pump, hot_pump) if pump.running())
            max_running = max(max_running, pumps)
            peak = max(peak, header.update(flow, pumps, dt))
            moved = min(flow * dt, cold_tank.level_kg())
            try:
                drawn = cold_tank.draw(moved)
                hot_tank.fill(drawn)
            except InterlockError as error:
                violations.append(error.message)
                break
            if hot_pump.at_target():
                break
        steps.append(
            {
                "component": "salt",
                "action": "confirm_hot_flow",
                "detail": {
                    "net_flow_kg_s": round(hydraulic.net_flow_kg_s(), 3),
                    "target_flow_kg_s": round(hot_pump.target_flow_kg_s(), 3),
                },
            }
        )

        if hot_pump.target_flow_kg_s() > 0 and hydraulic.net_flow_kg_s() < hot_pump.target_flow_kg_s():
            violations.append("hot_flow_not_established")
        if header.overpressure():
            violations.append("header_overpressure")
        if max_running >= 2:
            violations.append("pumps_opposed")

        if not violations:
            self._changeovers += 1
        return HandoffReport(not violations, steps, violations, peak, max_running)

    def changeovers(self) -> int:
        """返回累计成功切换次数。"""

        return self._changeovers
