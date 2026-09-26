"""汽机负荷斜率。

负荷变化按给定斜率推进，避免阶跃把汽机转子与蒸汽管系打热。目标值一律夹在
``0`` 到配置的最大负荷之间，执行值只向目标收敛。
"""

from __future__ import annotations

from ..config import SteamLimits


class LoadRamp:
    """负荷目标与斜率推进。"""

    def __init__(self, limits: SteamLimits) -> None:
        self._limits = limits
        self._target = 0.0
        self._current = 0.0
        self._steps = 0

    @property
    def limits(self) -> SteamLimits:
        return self._limits

    def request(self, mw: float) -> float:
        """下达负荷目标（夹到 [0, load_max_mw]）。"""

        self._target = max(0.0, min(self._limits.load_max_mw, float(mw)))
        return self._target

    def step(self, dt: float) -> float:
        """按斜率推进一个步长，返回推进后的负荷。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        span = self._limits.load_rate_mw_s * dt
        gap = self._target - self._current
        if abs(gap) <= span:
            self._current = self._target
        else:
            self._current += span if gap > 0 else -span
            self._steps += 1
        return self._current

    def settle(self, dt: float = 1.0, max_steps: int = 3600) -> float:
        """推进到目标负荷，返回负荷。"""

        for _ in range(max_steps):
            self.step(dt)
            if self.at_target():
                break
        return self._current

    def current_mw(self) -> float:
        """返回当前负荷。"""

        return self._current

    def target_mw(self) -> float:
        """返回目标负荷。"""

        return self._target

    def at_target(self) -> bool:
        """是否已经到达目标负荷。"""

        return abs(self._current - self._target) < 1e-9

    def steps(self) -> int:
        """返回累计被斜率限幅的步数。"""

        return self._steps
