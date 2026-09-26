"""蒸汽系统控制器。

把热功率换算成产汽量，把汽机负荷换算成进汽量，其余走旁路，三者一起推进主
蒸汽压力。系统对外只提供三件事：建负荷、甩负荷、跳机；每一步都由
:class:`~cspplant.steam.turbine.Turbine` 保证"旁路先开、主汽门后关"。
"""

from __future__ import annotations

import threading

from ..config import SteamLimits
from .bypass import BypassValve
from .pressure import SteamPressure
from .snapshot import SteamSnapshot
from .turbine import Turbine, TurbineReport
from .turbine_load import LoadRamp

# 产汽焓差（kJ/kg），把吸热器热功率换算成蒸汽流量。
STEAM_ENTHALPY_KJ_KG = 2600.0

# 汽机负荷到进汽量的换算（kg/s per MW）。
TURBINE_KG_S_PER_MW = 0.33

# 旁路通流能力（kg/s）。
BYPASS_CAPACITY_KG_S = 40.0

# 主蒸汽压力偏差到汽机进汽量修正的换算（kg/s per MPa）。
PRESSURE_CONTROL_GAIN_KG_S_PER_MPA = 8.0


class SteamSystem:
    """蒸汽发生器出口到汽机的整条链。"""

    def __init__(
        self,
        limits: SteamLimits,
        pressure: SteamPressure,
        ramp: LoadRamp,
        bypass: BypassValve,
        turbine: Turbine,
    ) -> None:
        self._limits = limits
        self._pressure = pressure
        self._ramp = ramp
        self._bypass = bypass
        self._turbine = turbine
        self._production = 0.0
        self._lock = threading.RLock()

    @property
    def limits(self) -> SteamLimits:
        return self._limits

    @property
    def pressure(self) -> SteamPressure:
        return self._pressure

    @property
    def ramp(self) -> LoadRamp:
        return self._ramp

    @property
    def bypass(self) -> BypassValve:
        return self._bypass

    @property
    def turbine(self) -> Turbine:
        return self._turbine

    def production_kg_s(self) -> float:
        """返回最近一次换算得到的产汽量。"""

        return self._production

    def draw_kg_s(self, load_mw: float) -> float:
        """按汽机负荷给出进汽量。"""

        return max(0.0, float(load_mw)) * TURBINE_KG_S_PER_MW

    def bypass_draw_kg_s(self, surplus_kg_s: float) -> float:
        """按旁路通流能力给出旁路流量。"""

        if not self._bypass.is_open():
            return 0.0
        return max(0.0, min(BYPASS_CAPACITY_KG_S, float(surplus_kg_s)))

    def update(self, thermal_power_kw: float, dt: float) -> dict:
        """推进一个步长：产汽 → 进汽／旁路 → 压力。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        with self._lock:
            production = max(0.0, float(thermal_power_kw)) / STEAM_ENTHALPY_KJ_KG
            self._production = production
            self._ramp.step(dt)
            # 压力偏差由进汽量吸收：压力偏高就多抽汽、偏低就少抽汽，避免扰动
            # 之后压力长期停在目标之外。
            error = self._pressure.value_mpa() - self._limits.pressure_target_mpa
            to_turbine = max(0.0, self.draw_kg_s(self._ramp.current_mw()) + error * PRESSURE_CONTROL_GAIN_KG_S_PER_MPA)
            surplus = production - to_turbine
            to_bypass = self.bypass_draw_kg_s(surplus)
            pressure = self._pressure.update(production, to_turbine, to_bypass, dt)
            return {
                "production_kg_s": round(production, 3),
                "to_turbine_kg_s": round(to_turbine, 3),
                "to_bypass_kg_s": round(to_bypass, 3),
                "pressure_mpa": round(pressure, 3),
                "settled": self._pressure.settled(),
                "exceedances": self._pressure.exceedances(),
            }

    def load(self, mw: float) -> TurbineReport:
        """建负荷。"""

        with self._lock:
            return self._turbine.raise_load(mw)

    def track_load(self, mw: float) -> float:
        """把控制循环算出的目标负荷交给斜率器，并按需打开主汽门。

        与 :meth:`load` 的区别是这里只登记目标、由每个周期逐步推进，用于负荷
        指令随调度连续变化的常规运行。
        """

        with self._lock:
            target = max(0.0, min(self._limits.load_max_mw, float(mw)))
            if target > 0.0 and not self._turbine.main_stop_open() and not self._bypass.is_open():
                self._turbine.open_main_stop()
            self._ramp.request(target)
            return target

    def shed(self, target_mw: float, now: float) -> TurbineReport:
        """甩负荷（先开旁路、再关主汽门）。"""

        with self._lock:
            return self._turbine.shed(target_mw, now)

    def trip(self, now: float) -> TurbineReport:
        """跳机。"""

        with self._lock:
            return self._turbine.trip(now)

    def disturb(self, delta_mpa: float) -> dict:
        """注入一次压力扰动，返回扰动后的压力。"""

        with self._lock:
            before = self._pressure.value_mpa()
            after = self._pressure.disturb(delta_mpa)
            return {
                "before_mpa": round(before, 3),
                "after_mpa": round(after, 3),
                "settled": self._pressure.settled(),
            }

    def status(self) -> dict:
        """汇总蒸汽系统状态。"""

        return {
            "pressure_mpa": round(self._pressure.value_mpa(), 3),
            "target_mpa": self._limits.pressure_target_mpa,
            "band_mpa": self._limits.pressure_band_mpa,
            "settled": self._pressure.settled(),
            "peak_mpa": round(self._pressure.peak_mpa(), 3),
            "exceedances": self._pressure.exceedances(),
            "production_kg_s": round(self._production, 3),
            "load_mw": round(self._ramp.current_mw(), 3),
            "target_mw": round(self._ramp.target_mw(), 3),
            "main_stop_open": self._turbine.main_stop_open(),
            "bypass": self._bypass.as_dict(),
            "sheds": self._turbine.sheds(),
            "trips": self._turbine.trips(),
        }

    def snapshot(self) -> SteamSnapshot:
        """返回蒸汽系统状态快照对象。"""

        return SteamSnapshot(
            pressure_mpa=self._pressure.value_mpa(),
            settled=self._pressure.settled(),
            bypass_open=self._bypass.is_open(),
            main_stop_open=self._turbine.main_stop_open(),
            load_mw=self._ramp.current_mw(),
            target_mw=self._ramp.target_mw(),
            production_kg_s=self._production,
            sheds=self._turbine.sheds(),
        )
