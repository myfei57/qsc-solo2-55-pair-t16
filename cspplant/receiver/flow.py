"""流量确认门控。

低流量联锁要能拦住干烧，前提是"流量够不够"在提负荷之前就已经判定完。这里
用累计确认时间做门控：流量连续达标 ``flow_confirm_s`` 秒才算确认，中途跌破
门限就清零重算。确认状态是提负荷的硬前提，不是提示。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ReceiverLimits


@dataclass(frozen=True)
class FlowStatus:
    """一次流量判定结果。"""

    flow_kg_s: float
    sufficient: bool
    confirmed: bool
    hold_s: float
    threshold_kg_s: float

    def as_dict(self) -> dict:
        return {
            "flow_kg_s": round(self.flow_kg_s, 3),
            "sufficient": self.sufficient,
            "confirmed": self.confirmed,
            "hold_s": round(self.hold_s, 3),
            "threshold_kg_s": round(self.threshold_kg_s, 3),
        }


class FlowGate:
    """按累计达标时间确认流量。"""

    def __init__(self, limits: ReceiverLimits) -> None:
        self._limits = limits
        self._hold_s = 0.0
        self._resets = 0
        self._confirmations = 0
        self._last_flow = 0.0
        self._was_confirmed = False

    @property
    def limits(self) -> ReceiverLimits:
        return self._limits

    def update(self, flow_kg_s: float, dt: float) -> FlowStatus:
        """推进一个步长并返回流量判定结果。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        flow = float(flow_kg_s)
        sufficient = self._limits.flow_sufficient(flow)
        if sufficient:
            self._hold_s += dt
        else:
            if self._hold_s > 0:
                self._resets += 1
            self._hold_s = 0.0
        confirmed = self.confirmed()
        if confirmed and not self._was_confirmed:
            self._confirmations += 1
        self._was_confirmed = confirmed
        self._last_flow = flow
        return FlowStatus(flow, sufficient, confirmed, self._hold_s, self._limits.flow_min_kg_s)

    def confirmed(self) -> bool:
        """流量是否已经确认。"""

        return self._hold_s >= self._limits.flow_confirm_s

    def hold_s(self) -> float:
        """返回当前累计达标时间。"""

        return self._hold_s

    def resets(self) -> int:
        """返回因跌破门限而清零的次数。"""

        return self._resets

    def confirmations(self) -> int:
        """返回流量被确认通过的次数。"""

        return self._confirmations

    def last_flow(self) -> float:
        """返回最近一次参与判定的流量。"""

        return self._last_flow
