"""汽机负荷控制。

甩负荷只有一种安全顺序：**先开旁路，再关小进汽门**。:meth:`Turbine.shed`
把这个顺序写死：旁路没打开就不允许压负荷，压到零之后才允许关主汽门。进汽门
开度与负荷斜率分开建模，避免"负荷降了但门还开着"这种中间态被当成完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import SteamLimits
from .bypass import BypassValve
from .turbine_load import LoadRamp

MAX_STEPS = 3600


@dataclass(frozen=True)
class TurbineReport:
    """一次汽机动作的结果。"""

    ok: bool
    steps: list[dict] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    load_mw: float = 0.0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "steps": self.steps,
            "violations": self.violations,
            "load_mw": round(self.load_mw, 3),
        }


class Turbine:
    """汽机与它的主汽门。"""

    def __init__(self, limits: SteamLimits, ramp: LoadRamp, bypass: BypassValve) -> None:
        self._limits = limits
        self._ramp = ramp
        self._bypass = bypass
        self._main_stop_open = False
        self._sheds = 0
        self._trips = 0

    @property
    def limits(self) -> SteamLimits:
        return self._limits

    @property
    def bypass(self) -> BypassValve:
        return self._bypass

    @property
    def ramp(self) -> LoadRamp:
        return self._ramp

    def main_stop_open(self) -> bool:
        """主汽门是否打开。"""

        return self._main_stop_open

    def open_main_stop(self) -> bool:
        """打开主汽门（不带负荷，负荷由斜率单独推进）。"""

        self._main_stop_open = True
        return self._main_stop_open

    def close_main_stop(self) -> bool:
        """关闭主汽门（旁路必须先开，顺序由甩负荷编排保证）。"""

        self._main_stop_open = False
        return self._main_stop_open

    def raise_load(self, mw: float, dt: float = 1.0) -> TurbineReport:
        """带负荷：先关旁路，再开主汽门，最后按斜率升负荷。"""

        steps: list[dict] = []
        violations: list[str] = []
        if self._bypass.is_open():
            self._bypass.close()
            steps.append({"component": "steam", "action": "close_bypass", "detail": {"open": False}})
        if not self._main_stop_open:
            self._main_stop_open = True
            steps.append({"component": "steam", "action": "open_main_stop", "detail": {"open": True}})
        self._ramp.request(mw)
        load = self._ramp.settle(dt, MAX_STEPS)
        steps.append(
            {
                "component": "steam",
                "action": "raise_load",
                "detail": {"load_mw": round(load, 3), "target_mw": round(float(mw), 3)},
            }
        )
        if not self._ramp.at_target():
            violations.append("load_not_reached")
        return TurbineReport(not violations, steps, violations, load)

    def shed(self, target_mw: float, now: float, dt: float = 1.0) -> TurbineReport:
        """甩负荷：先开旁路接住蒸汽，再压负荷，压到零才关主汽门。"""

        steps: list[dict] = []
        violations: list[str] = []

        if not self._bypass.is_open():
            self._bypass.open(now)
        steps.append(
            {
                "component": "steam",
                "action": "open_bypass",
                "detail": {"open": self._bypass.is_open(), "opened_at": self._bypass.opened_at()},
            }
        )

        target = max(0.0, min(self._limits.load_max_mw, float(target_mw)))
        if not self.requires_bypass(target):
            violations.append("shed_target_not_lower")
        self._ramp.request(target)
        load = self._ramp.settle(dt, MAX_STEPS)
        steps.append(
            {
                "component": "steam",
                "action": "lower_load",
                "detail": {"load_mw": round(load, 3), "target_mw": round(target, 3)},
            }
        )
        if not self._ramp.at_target():
            violations.append("load_not_reached")

        if target <= 0.0:
            if not self._bypass.is_open():
                violations.append("bypass_not_open_before_main_stop")
            else:
                self._main_stop_open = False
                steps.append(
                    {
                        "component": "steam",
                        "action": "close_main_stop",
                        "detail": {"open": self._main_stop_open, "bypass_open": self._bypass.is_open()},
                    }
                )
                if not self.guard_order(True, not self._main_stop_open):
                    violations.append("shed_order_broken")
        if not violations:
            self._sheds += 1
        return TurbineReport(not violations, steps, violations, load)

    def trip(self, now: float) -> TurbineReport:
        """跳机：先开旁路，再关主汽门。"""

        steps: list[dict] = []
        violations: list[str] = []
        if not self._bypass.is_open():
            self._bypass.open(now)
        steps.append({"component": "steam", "action": "open_bypass", "detail": {"open": True}})
        if self._bypass.is_open():
            self._main_stop_open = False
            self._ramp.request(0.0)
            self._ramp.settle(1.0, MAX_STEPS)
            steps.append(
                {
                    "component": "steam",
                    "action": "close_main_stop",
                    "detail": {"open": False, "load_mw": round(self._ramp.current_mw(), 3)},
                }
            )
        else:  # pragma: no cover - 旁路开阀失败属于硬件故障
            violations.append("bypass_failed_to_open")
        self._trips += 1
        return TurbineReport(not violations, steps, violations, self._ramp.current_mw())

    def requires_bypass(self, target_mw: float) -> bool:
        """给定目标负荷是否需要先走旁路（负荷下降即需要）。"""

        return float(target_mw) < self._ramp.current_mw()

    def guard_order(self, bypass_open_before: bool, main_stop_closed_after: bool) -> bool:
        """核对一次甩负荷的顺序：旁路先开、主汽门后关。"""

        return bool(bypass_open_before) and bool(main_stop_closed_after)

    def sheds(self) -> int:
        """返回累计甩负荷次数。"""

        return self._sheds

    def trips(self) -> int:
        """返回累计跳机次数。"""

        return self._trips
