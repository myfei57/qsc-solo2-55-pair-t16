"""避让区校验。

镜面在检修姿态下把光打到地面工位，落点由"镜位 + 检修方位 + 作用距离"唯一
确定。:class:`ZoneGuard` 把这个落点拿去和避让区登记表比对，得出是否允许进入
检修姿态；不允许时调用方必须改用收起姿态，而不是硬转。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..ns.zones import SafeZone, ZoneRegistry


@dataclass(frozen=True)
class ZoneVerdict:
    """一次避让区校验结果。"""

    allowed: bool
    landing_x_m: float
    landing_y_m: float
    zones: tuple[str, ...] = ()
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "landing_x_m": round(self.landing_x_m, 2),
            "landing_y_m": round(self.landing_y_m, 2),
            "zones": list(self.zones),
            "detail": self.detail,
        }


class ZoneGuard:
    """按落点判定检修姿态是否被避让区挡住。"""

    def __init__(self, zones: ZoneRegistry, margin_m: float = 0.0) -> None:
        self._zones = zones
        self._margin_m = max(0.0, float(margin_m))
        self._vetoes = 0

    @property
    def margin_m(self) -> float:
        return self._margin_m

    @property
    def zones(self) -> ZoneRegistry:
        return self._zones

    def landing_point(
        self, x_m: float, y_m: float, azimuth_deg: float, reach_m: float
    ) -> tuple[float, float]:
        """按镜位、方位角与作用距离算出落点。"""

        radians = math.radians(float(azimuth_deg))
        return (
            float(x_m) + math.sin(radians) * float(reach_m),
            float(y_m) + math.cos(radians) * float(reach_m),
        )

    def evaluate(self, x_m: float, y_m: float, azimuth_deg: float, reach_m: float) -> ZoneVerdict:
        """判定某个检修姿态的落点是否落在避让区内。"""

        landing = self.landing_point(x_m, y_m, azimuth_deg, reach_m)
        covering: list[SafeZone] = self._zones.covering(landing[0], landing[1], self._margin_m)
        if covering:
            self._vetoes += 1
        return ZoneVerdict(
            allowed=not covering,
            landing_x_m=landing[0],
            landing_y_m=landing[1],
            zones=tuple(zone.name for zone in covering),
            detail={"margin_m": self._margin_m, "reach_m": round(float(reach_m), 2)},
        )

    def vetoes(self) -> int:
        """返回累计挡下的检修姿态次数。"""

        return self._vetoes
