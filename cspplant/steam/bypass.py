"""蒸汽旁路阀。

旁路阀只有开与关两种到位状态，但它记录开阀时刻，用来核对"旁路是否在进汽门
动作之前就已经打开"。这个时间差就是甩负荷顺序是否正确的客观证据。
"""

from __future__ import annotations

from ..config import SteamLimits
from ..errors import InterlockError


class BypassValve:
    """蒸汽旁路阀。"""

    def __init__(self, limits: SteamLimits) -> None:
        self._limits = limits
        self._open = False
        self._opened_at: float | None = None
        self._opens = 0
        self._closes = 0
        self._rejected = 0

    @property
    def limits(self) -> SteamLimits:
        return self._limits

    def open(self, now: float) -> bool:
        """打开旁路；已经打开即拒绝。"""

        if self._open:
            self._rejected += 1
            raise InterlockError("旁路阀已打开，禁止重复开启", component="steam")
        self._open = True
        self._opened_at = float(now)
        self._opens += 1
        return self._open

    def close(self) -> bool:
        """关闭旁路；本来就是关的即拒绝。"""

        if not self._open:
            self._rejected += 1
            raise InterlockError("旁路阀本来就是关的", component="steam")
        self._open = False
        self._opened_at = None
        self._closes += 1
        return self._open

    def is_open(self) -> bool:
        """旁路是否打开。"""

        return self._open

    def opened_at(self) -> float | None:
        """返回旁路打开时刻（未打开返回 ``None``）。"""

        return self._opened_at

    def held_open_s(self, now: float) -> float:
        """返回旁路已经打开多久。"""

        if self._opened_at is None:
            return 0.0
        return max(0.0, float(now) - self._opened_at)

    def opens(self) -> int:
        """返回累计开阀次数。"""

        return self._opens

    def closes(self) -> int:
        """返回累计关阀次数。"""

        return self._closes

    def rejected(self) -> int:
        """返回被拒绝的重复动作次数。"""

        return self._rejected

    def as_dict(self) -> dict:
        return {
            "open": self._open,
            "opened_at": None if self._opened_at is None else round(self._opened_at, 3),
            "opens": self._opens,
            "closes": self._closes,
            "rejected": self._rejected,
        }
