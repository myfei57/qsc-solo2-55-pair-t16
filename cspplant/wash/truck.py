"""清洗车。

车上带水箱，按喷淋时长消耗水量；水箱见底就拒绝继续喷淋，避免空泵。喷淋进度
按时间累计，只有进度满格才算洗完，避免"车到了就当作洗完"。
"""

from __future__ import annotations

from ..config import WashLimits
from ..errors import InterlockError

IDLE = "idle"
WASHING = "washing"
RETURNING = "returning"


class WashTruck:
    """镜面清洗车。"""

    def __init__(self, limits: WashLimits, water_m3: float | None = None) -> None:
        self._limits = limits
        self._mirror = ""
        self._state = IDLE
        self._elapsed_s = 0.0
        self._used_m3 = 0.0
        self._returned_m3 = 0.0
        self._water_m3 = float(limits.truck_capacity_m3 if water_m3 is None else water_m3)
        self._washes = 0

    @property
    def limits(self) -> WashLimits:
        return self._limits

    def arrive(self, mirror: str) -> str:
        """到达某面镜子并复位本镜进度。"""

        if not mirror:
            raise InterlockError("清洗必须指定镜子", component="wash")
        self._mirror = str(mirror)
        self._elapsed_s = 0.0
        self._state = IDLE
        return self._mirror

    def start_wash(self) -> str:
        """开始喷淋；未到位或没水即拒绝。"""

        if not self._mirror:
            raise InterlockError("清洗车尚未到达镜子", component="wash")
        if self._water_m3 <= 0.0:
            raise InterlockError("车载水箱已空，禁止继续喷淋", component="wash")
        self._state = WASHING
        return self._state

    def step(self, dt: float) -> float:
        """推进喷淋一个步长，返回喷淋进度。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        if self._state != WASHING:
            raise InterlockError("清洗车未在喷淋", component="wash")
        consume = self._limits.return_flow_m3_h * dt / 3600.0
        if consume > self._water_m3:
            consume = self._water_m3
        self._water_m3 -= consume
        self._used_m3 += consume
        self._elapsed_s += dt
        if self.progress() >= 1.0:
            self._state = RETURNING
            self._washes += 1
        return self.progress()

    def progress(self) -> float:
        """返回喷淋进度（0–1）。"""

        return min(1.0, self._elapsed_s / self._limits.wash_duration_s)

    def complete(self) -> bool:
        """喷淋是否完成。"""

        return self.progress() >= 1.0

    def state(self) -> str:
        """返回清洗车状态。"""

        return self._state

    def mirror(self) -> str:
        """返回当前作业的镜子名。"""

        return self._mirror

    def water_m3(self) -> float:
        """返回车载水箱剩余水量。"""

        return self._water_m3

    def used_m3(self) -> float:
        """返回本次作业累计用水量。"""

        return self._used_m3

    def returned_m3(self) -> float:
        """返回本次作业累计回收水量。"""

        return self._returned_m3

    def record_returned(self, amount_m3: float) -> float:
        """登记回收水量（由回水系统调用）。"""

        self._returned_m3 += max(0.0, float(amount_m3))
        return self._returned_m3

    def washes(self) -> int:
        """返回累计清洗镜面数。"""

        return self._washes

    def refill(self, amount_m3: float) -> float:
        """补水，返回补水后的水量。"""

        self._water_m3 = min(self._limits.truck_capacity_m3, self._water_m3 + max(0.0, float(amount_m3)))
        return self._water_m3

    def snapshot(self) -> dict:
        return {
            "state": self._state,
            "mirror": self._mirror,
            "progress": round(self.progress(), 3),
            "water_m3": round(self._water_m3, 4),
            "used_m3": round(self._used_m3, 4),
            "washes": self._washes,
        }
