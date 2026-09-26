"""回水回收与排水。

回水先落到车上托盘，再由回水泵送进回收罐。排水没排空就停泵，托盘里的水会
溢到地面。因此这里把"托盘排空"做成可判定的状态：托盘水量由喷淋水量与已排
水量共同决定，``drained()`` 只在托盘水量归零时为真。
"""

from __future__ import annotations

from ..config import WashLimits
from ..errors import InterlockError


class DrainSystem:
    """托盘、排水阀与回水泵。"""

    def __init__(self, limits: WashLimits, tray_capacity_m3: float = 1.2) -> None:
        self._limits = limits
        self._tray_capacity = float(tray_capacity_m3)
        self._tray_m3 = 0.0
        self._recovered_m3 = 0.0
        self._drain_open = False
        self._pump_running = False
        self._opens = 0
        self._stops = 0
        self._premature_stops = 0

    @property
    def limits(self) -> WashLimits:
        return self._limits

    @property
    def tray_capacity_m3(self) -> float:
        return self._tray_capacity

    def receive(self, amount_m3: float) -> float:
        """把喷淋回水接进托盘，返回托盘当前水量。"""

        self._tray_m3 = min(self._tray_capacity, self._tray_m3 + max(0.0, float(amount_m3)))
        return self._tray_m3

    def open_drain(self) -> bool:
        """开排水阀并启回水泵。"""

        if self._drain_open:
            raise InterlockError("排水阀已打开", component="wash")
        self._drain_open = True
        self._pump_running = True
        self._opens += 1
        return self._drain_open

    def step(self, dt: float) -> float:
        """推进一个步长，返回从托盘排走的水量。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        if not self._drain_open or not self._pump_running:
            return 0.0
        capacity = self._limits.return_flow_m3_h * dt / 3600.0
        moved = min(capacity, self._tray_m3)
        self._tray_m3 -= moved
        self._recovered_m3 += moved
        return moved

    def drained(self) -> bool:
        """托盘是否已经排空。"""

        return self._tray_m3 <= 1e-9

    def stop_pump(self) -> bool:
        """停回水泵；托盘未排空即记一次超前停泵并拒绝。"""

        if not self._pump_running:
            raise InterlockError("回水泵本来就没在运行", component="wash")
        if not self.drained():
            self._premature_stops += 1
            raise InterlockError("托盘未排空，禁止停回水泵", component="wash")
        self._pump_running = False
        self._drain_open = False
        self._stops += 1
        return True

    def tray_m3(self) -> float:
        """返回托盘中剩余水量。"""

        return self._tray_m3

    def recovered_m3(self) -> float:
        """返回累计回收水量。"""

        return self._recovered_m3

    def pump_running(self) -> bool:
        """回水泵是否在运行。"""

        return self._pump_running

    def drain_open(self) -> bool:
        """排水阀是否打开。"""

        return self._drain_open

    def opens(self) -> int:
        """返回累计开阀次数。"""

        return self._opens

    def stops(self) -> int:
        """返回累计正常停泵次数。"""

        return self._stops

    def premature_stops(self) -> int:
        """返回累计被拒的超前停泵次数。"""

        return self._premature_stops

    def reset(self) -> None:
        """复位托盘与阀门状态（一个循环开始时调用）。"""

        self._tray_m3 = 0.0
        self._drain_open = False
        self._pump_running = False
