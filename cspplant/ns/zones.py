"""避让区登记。

检修区与扫描区都是平面上的圆形区域，任何定日镜的镜斑落点都不能进入。区域
登记的粒度是"区"而不是"镜"，因为检修票是按区开的：一张票关闭一个区，区里
所有定日镜一起退出聚焦。:class:`ZoneRegistry` 负责增删查，交集判定留给
``heliostat.zone`` 使用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import NamespaceError


@dataclass(frozen=True)
class SafeZone:
    """一个圆形避让区。"""

    name: str
    center_x_m: float
    center_y_m: float
    radius_m: float
    kind: str = "maintenance"

    def distance_to(self, x_m: float, y_m: float) -> float:
        """返回某点到区域中心的平面距离。"""

        return math.hypot(float(x_m) - self.center_x_m, float(y_m) - self.center_y_m)

    def contains(self, x_m: float, y_m: float, margin_m: float = 0.0) -> bool:
        """点是否落在区域内（含外扩的检修余量）。"""

        return self.distance_to(x_m, y_m) <= self.radius_m + max(0.0, margin_m)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "center_x_m": round(self.center_x_m, 2),
            "center_y_m": round(self.center_y_m, 2),
            "radius_m": round(self.radius_m, 2),
            "kind": self.kind,
        }


class ZoneRegistry:
    """避让区注册表。"""

    def __init__(self) -> None:
        self._zones: dict[str, SafeZone] = {}

    def add(self, zone: SafeZone) -> SafeZone:
        """登记一个避让区，重名即报错。"""

        if not zone.name:
            raise NamespaceError("避让区必须有名字", component="ns")
        if zone.name in self._zones:
            raise NamespaceError(f"避让区已存在: {zone.name}", component="ns")
        if zone.radius_m <= 0:
            raise NamespaceError(f"避让区半径必须为正: {zone.name}", component="ns")
        self._zones[zone.name] = zone
        return zone

    def remove(self, name: str) -> bool:
        """撤销一个避让区，返回是否真的删掉了。"""

        return self._zones.pop(name, None) is not None

    def get(self, name: str) -> SafeZone:
        """按名字取区域。"""

        try:
            return self._zones[name]
        except KeyError as error:
            raise NamespaceError(f"未登记避让区: {name}", component="ns") from error

    def names(self) -> list[str]:
        """返回全部区域名（排序后）。"""

        return sorted(self._zones)

    def covering(self, x_m: float, y_m: float, margin_m: float = 0.0) -> list[SafeZone]:
        """返回覆盖某点的全部区域。"""

        return [zone for zone in self._zones.values() if zone.contains(x_m, y_m, margin_m)]

    def describe(self) -> dict:
        """汇总避让区，供状态快照读取。"""

        return {"count": len(self._zones), "zones": [self._zones[name].as_dict() for name in self.names()]}
