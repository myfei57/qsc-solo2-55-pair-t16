"""熔盐罐。

冷热罐各按液位百分比管理：冷罐低于下限会把泵抽空，热罐高于上限会溢流，两者
都在放盐／进盐时实时核对。液位只由 ``draw`` 与 ``fill`` 改动，吨位与百分比
永远一致，杜绝两个口径各写一份。
"""

from __future__ import annotations

from ..errors import InterlockError


class SaltTank:
    """一个熔盐罐。"""

    def __init__(
        self,
        name: str,
        capacity_kg: float,
        level_kg: float,
        min_pct: float = 5.0,
        max_pct: float = 98.0,
    ) -> None:
        if capacity_kg <= 0:
            raise ValueError("罐容必须为正")
        if not 0.0 <= min_pct < max_pct <= 100.0:
            raise ValueError("液位上下限不合法")
        self._name = name
        self._capacity = float(capacity_kg)
        self._level = max(0.0, min(self._capacity, float(level_kg)))
        self._min_pct = float(min_pct)
        self._max_pct = float(max_pct)
        self._draws = 0
        self._fills = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def capacity_kg(self) -> float:
        return self._capacity

    def level_kg(self) -> float:
        """返回当前存量（千克）。"""

        return self._level

    def level_pct(self) -> float:
        """返回当前液位百分比。"""

        return 100.0 * self._level / self._capacity

    def set_level_pct(self, pct: float) -> float:
        """直接设置液位（用于重启恢复与演示初值）。"""

        self._level = max(0.0, min(self._capacity, self._capacity * float(pct) / 100.0))
        return self.level_pct()

    def draw(self, kg: float) -> float:
        """抽出熔盐，返回抽出量；低于下限即拒绝。"""

        amount = float(kg)
        if amount < 0:
            raise ValueError("抽出量不能为负")
        if self.at_min() and amount > 0:
            raise InterlockError(f"{self._name} 液位已到下限，禁止继续抽出", component="salt")
        taken = min(amount, self._level)
        self._level -= taken
        self._draws += 1
        return taken

    def fill(self, kg: float) -> float:
        """灌入熔盐，返回实际灌入量；到上限即拒绝继续灌。"""

        amount = float(kg)
        if amount < 0:
            raise ValueError("灌入量不能为负")
        if self.at_max() and amount > 0:
            raise InterlockError(f"{self._name} 液位已到上限，禁止继续灌入", component="salt")
        added = min(amount, self.headroom_kg())
        self._level += added
        self._fills += 1
        return added

    def headroom_kg(self) -> float:
        """返回还能灌入多少千克。"""

        return max(0.0, self._capacity * self._max_pct / 100.0 - self._level)

    def at_min(self) -> bool:
        """液位是否已到下限。"""

        return self.level_pct() <= self._min_pct

    def at_max(self) -> bool:
        """液位是否已到上限。"""

        return self.level_pct() >= self._max_pct

    def draws(self) -> int:
        """返回累计抽出次数。"""

        return self._draws

    def fills(self) -> int:
        """返回累计灌入次数。"""

        return self._fills

    def as_dict(self) -> dict:
        return {
            "name": self._name,
            "capacity_kg": round(self._capacity, 1),
            "level_kg": round(self._level, 1),
            "level_pct": round(self.level_pct(), 3),
            "at_min": self.at_min(),
            "at_max": self.at_max(),
            "draws": self._draws,
            "fills": self._fills,
        }
