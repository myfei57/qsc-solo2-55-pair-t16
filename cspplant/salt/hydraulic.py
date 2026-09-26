"""水力模型。

在运泵的流量叠加得到母管总流量；两台泵同时运行时互相顶压，净流量并不等于
两者之和，而是打一个折扣。模型只做账，不直接读泵的私有字段——泵把自己当前
流量报出来，由这里汇总。
"""

from __future__ import annotations

from ..config import SaltLimits
from .pump import SaltPump

# 两泵同时运行时净流量的折扣系数。
OPPOSED_FLOW_FACTOR = 0.55


class HydraulicLoop:
    """按在运泵汇总母管流量。"""

    def __init__(self, limits: SaltLimits) -> None:
        self._limits = limits
        self._flow = 0.0
        self._updates = 0
        self._opposed_steps = 0

    @property
    def limits(self) -> SaltLimits:
        return self._limits

    def update(self, pumps: list[SaltPump], dt: float) -> float:
        """推进一个步长并汇总净流量。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        for pump in pumps:
            pump.step(dt)
        running = [pump for pump in pumps if pump.running()]
        total = sum(pump.flow_kg_s() for pump in pumps)
        if len(running) >= 2:
            total *= OPPOSED_FLOW_FACTOR
            self._opposed_steps += 1
        self._flow = max(0.0, total)
        self._updates += 1
        return self._flow

    def net_flow_kg_s(self) -> float:
        """返回最近一次汇总的净流量。"""

        return self._flow

    def opposed_steps(self) -> int:
        """返回处于两泵对顶状态的步数。"""

        return self._opposed_steps

    def updates(self) -> int:
        """返回累计推进步数。"""

        return self._updates
