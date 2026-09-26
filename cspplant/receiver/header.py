"""熔盐母管接口。

吸热器出口经母管连到盐侧：只有流量确认之后才允许通盐（干烧的物理原因），
母管压力由通过的流量与在运泵数决定。两台泵同时运行时两路互相顶压，压力会
明显高于单泵，这正是冷热罐切换必须先停后启的原因。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import SaltLimits
from ..errors import InterlockError

CLOSED = "closed"
ADMITTED = "admitted"

# 两台泵同时运行时的顶压倍数。
OPPOSED_PUMP_FACTOR = 1.6


@dataclass(frozen=True)
class HeaderState:
    """母管状态。"""

    state: str
    flow_kg_s: float
    pressure_mpa: float
    overpressure: bool
    pumps_running: int

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "flow_kg_s": round(self.flow_kg_s, 3),
            "pressure_mpa": round(self.pressure_mpa, 3),
            "overpressure": self.overpressure,
            "pumps_running": self.pumps_running,
        }


class SaltHeader:
    """吸热器出口母管。"""

    def __init__(self, limits: SaltLimits) -> None:
        self._limits = limits
        self._state = CLOSED
        self._flow = 0.0
        self._pressure = 0.0
        self._pumps = 0
        self._admits = 0
        self._overpressure_events = 0

    @property
    def limits(self) -> SaltLimits:
        return self._limits

    def state(self) -> str:
        """返回母管状态。"""

        return self._state

    def admit(self, flow_confirmed: bool) -> str:
        """允许熔盐进入母管；流量未确认即拒绝。"""

        if not flow_confirmed:
            raise InterlockError("流量未确认，禁止向母管通盐", component="receiver")
        self._state = ADMITTED
        self._admits += 1
        return self._state

    def close(self) -> str:
        """关闭母管通路。"""

        self._state = CLOSED
        return self._state

    def update(self, flow_kg_s: float, pumps_running: int, dt: float) -> float:
        """按流量与在运泵数推进母管压力，返回推进后的压力。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        self._flow = float(flow_kg_s)
        self._pumps = int(pumps_running)
        baseline = max(1e-6, self._limits.flow_baseline_kg_s)
        head = self._limits.header_pressure_target_mpa * (self._flow / baseline)
        if self._pumps >= 2:
            head *= OPPOSED_PUMP_FACTOR
        blend = min(1.0, dt / 5.0)
        self._pressure += (head - self._pressure) * blend
        if self._pressure < 0.0:
            self._pressure = 0.0
        if self.overpressure():
            self._overpressure_events += 1
        return self._pressure

    def depressurise(self, dt: float) -> float:
        """停泵后按同一时间常数泄压。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        self._pressure = max(0.0, self._pressure * (1.0 - min(1.0, dt / 5.0)))
        return self._pressure

    def pressure_mpa(self) -> float:
        """返回当前母管压力。"""

        return self._pressure

    def flow_kg_s(self) -> float:
        """返回最近一次参与计算的流量。"""

        return self._flow

    def overpressure(self) -> bool:
        """母管是否超压。"""

        return self._limits.header_overpressure(self._pressure)

    def admits(self) -> int:
        """返回累计通盐次数。"""

        return self._admits

    def overpressure_events(self) -> int:
        """返回累计超压判定次数。"""

        return self._overpressure_events

    def status(self) -> HeaderState:
        """返回母管状态快照。"""

        return HeaderState(self._state, self._flow, self._pressure, self.overpressure(), self._pumps)
