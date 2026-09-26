"""定日镜台账。

每台定日镜的位置、镜面积与所属分区只在这里登记一次，控制台、追日编排与
清洗排程都按同一份台账取数。重名即报错，避免同一面镜子被排进两次清洗批次。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import NamespaceError


@dataclass(frozen=True)
class Heliostat:
    """一台定日镜（聚光单元）的静态属性。

    ``area_m2`` 是这台聚光单元的合计镜面积：工程上一台单元由多面子镜拼成，
    控制链只按单元下发角度，因此台账也按单元记账。
    """

    name: str
    x_m: float
    y_m: float
    z_m: float
    area_m2: float
    group: str = "a"

    @property
    def distance_to_origin_m(self) -> float:
        return (self.x_m * self.x_m + self.y_m * self.y_m) ** 0.5

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "x_m": round(self.x_m, 2),
            "y_m": round(self.y_m, 2),
            "z_m": round(self.z_m, 2),
            "area_m2": round(self.area_m2, 2),
            "group": self.group,
        }


class HeliostatRegistry:
    """定日镜台账。"""

    def __init__(self) -> None:
        self._mirrors: dict[str, Heliostat] = {}

    def register(self, mirror: Heliostat) -> Heliostat:
        """登记一台定日镜，重名即报错。"""

        if not mirror.name:
            raise NamespaceError("定日镜必须有名字", component="heliostat")
        if mirror.name in self._mirrors:
            raise NamespaceError(f"定日镜已登记: {mirror.name}", component="heliostat")
        if mirror.area_m2 <= 0:
            raise NamespaceError(f"镜面积必须为正: {mirror.name}", component="heliostat")
        self._mirrors[mirror.name] = mirror
        return mirror

    def get(self, name: str) -> Heliostat:
        """按名字取定日镜。"""

        try:
            return self._mirrors[name]
        except KeyError as error:
            raise NamespaceError(f"未登记定日镜: {name}", component="heliostat") from error

    def names(self) -> list[str]:
        """返回全部定日镜名字（排序后）。"""

        return sorted(self._mirrors)

    def all(self) -> list[Heliostat]:
        """返回全部定日镜（按名字排序）。"""

        return [self._mirrors[name] for name in self.names()]

    def in_group(self, group: str) -> list[Heliostat]:
        """返回某个分区的全部定日镜。"""

        return [mirror for mirror in self.all() if mirror.group == group]

    def groups(self) -> list[str]:
        """返回全部分区名（排序后）。"""

        return sorted({mirror.group for mirror in self._mirrors.values()})

    def total_area_m2(self) -> float:
        """返回全场镜面积。"""

        return sum(mirror.area_m2 for mirror in self._mirrors.values())

    def describe(self) -> dict:
        """汇总台账，供状态快照读取。"""

        by_group: dict[str, int] = {}
        for mirror in self._mirrors.values():
            by_group[mirror.group] = by_group.get(mirror.group, 0) + 1
        farthest = max((mirror.distance_to_origin_m for mirror in self._mirrors.values()), default=0.0)
        return {
            "count": len(self._mirrors),
            "groups": by_group,
            "total_area_m2": round(self.total_area_m2(), 2),
            "farthest_m": round(farthest, 2),
        }
