"""主蒸汽压力。

压力由"产汽量 − 进汽量 − 旁路量"的净差额决定：产多送少压力上涨，产少送多
压力下跌。甩负荷时先开旁路把多出来的蒸汽接走，压力才不会在主汽门关小的瞬间
冲高。
"""

from __future__ import annotations

from ..config import SteamLimits

# 净流量到压力变化率的换算系数（MPa·s/kg）。
PRESSURE_GAIN_MPA_S_PER_KG = 0.002


class SteamPressure:
    """主蒸汽压力模型。"""

    def __init__(self, limits: SteamLimits) -> None:
        self._limits = limits
        self._pressure = limits.pressure_target_mpa
        self._updates = 0
        self._peak = self._pressure
        self._exceedances = 0

    @property
    def limits(self) -> SteamLimits:
        return self._limits

    def update(
        self,
        production_kg_s: float,
        to_turbine_kg_s: float,
        to_bypass_kg_s: float,
        dt: float,
    ) -> float:
        """推进一个步长，返回推进后的压力。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        net = float(production_kg_s) - float(to_turbine_kg_s) - float(to_bypass_kg_s)
        self._pressure += net * PRESSURE_GAIN_MPA_S_PER_KG * dt
        self._pressure = max(0.1, self._pressure)
        self._peak = max(self._peak, self._pressure)
        if self._pressure > self._limits.pressure_target_mpa + 2.0 * self._limits.pressure_band_mpa:
            self._exceedances += 1
        self._updates += 1
        return self._pressure

    def disturb(self, delta_mpa: float) -> float:
        """注入一次压力扰动，返回扰动后的压力。"""

        self._pressure = max(0.1, self._pressure + float(delta_mpa))
        self._peak = max(self._peak, self._pressure)
        return self._pressure

    def value_mpa(self) -> float:
        """返回当前压力。"""

        return self._pressure

    def settled(self) -> bool:
        """压力是否落在目标带内。"""

        return self._limits.settled(self._pressure)

    def peak_mpa(self) -> float:
        """返回历史最高压力。"""

        return self._peak

    def exceedances(self) -> int:
        """返回超出目标带两倍的次数。"""

        return self._exceedances

    def updates(self) -> int:
        """返回累计推进步数。"""

        return self._updates
