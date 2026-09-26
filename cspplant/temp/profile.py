"""出口温度预估。

按"镜面积 × 辐照 × 反射率"算入射热功率，再按熔盐比热算温升，给出未来若干
个采样点的出口温度。预估用的是现场可核对的口径——同样的输入在控制台与巡检
报告里必须得到同样的数。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ReceiverLimits
from ..receiver.thermal import INLET_TEMP_C, SALT_CP_KJ_KG_K
from .ambient import AmbientModel
from .solar import ClearSkyProfile


@dataclass(frozen=True)
class TemperaturePoint:
    """一个预估点。"""

    minute_of_day: float
    dni_w_m2: float
    power_kw: float
    outlet_c: float
    ambient_c: float
    derate_margin_c: float

    def as_dict(self) -> dict:
        return {
            "minute_of_day": round(self.minute_of_day, 1),
            "dni_w_m2": round(self.dni_w_m2, 1),
            "power_kw": round(self.power_kw, 1),
            "outlet_c": round(self.outlet_c, 2),
            "ambient_c": round(self.ambient_c, 2),
            "derate_margin_c": round(self.derate_margin_c, 2),
        }


class TemperatureProfile:
    """按辐照曲线与流量预估出口温度。"""

    def __init__(
        self,
        limits: ReceiverLimits,
        solar: ClearSkyProfile,
        ambient: AmbientModel,
        field_area_m2: float,
        reflectivity: float,
        optical_efficiency: float = 0.62,
        inlet_c: float = INLET_TEMP_C,
    ) -> None:
        if field_area_m2 <= 0:
            raise ValueError("镜面积必须为正")
        if not 0.0 < reflectivity <= 1.0:
            raise ValueError("反射率必须落在 (0, 1]")
        if not 0.0 < optical_efficiency <= 1.0:
            raise ValueError("光学效率必须落在 (0, 1]")
        self._limits = limits
        self._solar = solar
        self._ambient = ambient
        self._area = float(field_area_m2)
        self._reflectivity = float(reflectivity)
        self._optical = float(optical_efficiency)
        self._inlet_c = float(inlet_c)

    @property
    def limits(self) -> ReceiverLimits:
        return self._limits

    @property
    def solar(self) -> ClearSkyProfile:
        return self._solar

    @property
    def ambient(self) -> AmbientModel:
        return self._ambient

    @property
    def inlet_c(self) -> float:
        """返回计算温升用的冷盐入口温度。"""

        return self._inlet_c

    def power_kw(self, dni_w_m2: float) -> float:
        """按辐照度算入射到吸热器的热功率。"""

        return self._area * max(0.0, float(dni_w_m2)) * self._reflectivity * self._optical / 1000.0

    def outlet_c(self, power_kw: float, flow_kg_s: float, start_c: float) -> float:
        """按热功率与流量算稳态出口温度（入口温度取冷盐罐侧）。"""

        flow = max(1.0, float(flow_kg_s))
        inlet = max(float(start_c), self._inlet_c)
        return inlet + float(power_kw) / (flow * SALT_CP_KJ_KG_K)

    def predict(
        self,
        minute_of_day: float,
        flow_kg_s: float,
        horizon_min: float = 60.0,
        step_min: float = 10.0,
        cloud_factor: float = 1.0,
    ) -> list[TemperaturePoint]:
        """给出未来一段时间的出口温度预估。"""

        if horizon_min <= 0 or step_min <= 0:
            raise ValueError("预估窗口与步长都必须为正")
        points: list[TemperaturePoint] = []
        step = 0.0
        while step <= horizon_min:
            moment = float(minute_of_day) + step
            dni = self._solar.dni_w_m2(moment, cloud_factor)
            power = self.power_kw(dni)
            ambient = self._ambient.at(moment)
            outlet = self.outlet_c(power, flow_kg_s, ambient)
            points.append(
                TemperaturePoint(
                    minute_of_day=moment,
                    dni_w_m2=dni,
                    power_kw=power,
                    outlet_c=outlet,
                    ambient_c=ambient,
                    derate_margin_c=self._limits.temp_derate_c - outlet,
                )
            )
            step += step_min
        return points

    def minutes_to_derate(
        self, minute_of_day: float, flow_kg_s: float, cloud_factor: float = 1.0
    ) -> float | None:
        """返回还有多少分钟会进入降额带；始终不进入则返回 ``None``。"""

        for point in self.predict(minute_of_day, flow_kg_s, horizon_min=720.0, step_min=5.0, cloud_factor=cloud_factor):
            if point.outlet_c >= self._limits.temp_derate_c:
                return point.minute_of_day - float(minute_of_day)
        return None
