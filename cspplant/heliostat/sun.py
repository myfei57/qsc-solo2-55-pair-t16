"""太阳位置模型。

现场的真值来自太阳位置传感器，但控制链不能在传感器掉线时停摆，因此这里按
日出／日落时刻与正午最高仰角给出可复算的解析近似：仰角走正弦曲线，方位角
从东向西匀速扫过。模型只用来算追日目标角度，辐照度仍以辐照计采样为准。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import ConfigError


@dataclass(frozen=True)
class SunPosition:
    """某一时刻的太阳位置。"""

    minute_of_day: float
    azimuth_deg: float
    elevation_deg: float

    @property
    def above_horizon(self) -> bool:
        return self.elevation_deg > 0.0

    def as_dict(self) -> dict:
        return {
            "minute_of_day": round(self.minute_of_day, 2),
            "azimuth_deg": round(self.azimuth_deg, 3),
            "elevation_deg": round(self.elevation_deg, 3),
            "above_horizon": self.above_horizon,
        }


class SunModel:
    """按日出／日落与正午仰角推算太阳位置的解析模型。"""

    def __init__(
        self,
        sunrise_minute: float = 360.0,
        sunset_minute: float = 1080.0,
        peak_elevation_deg: float = 72.0,
    ) -> None:
        if not 0.0 <= sunrise_minute < sunset_minute <= 1440.0:
            raise ConfigError("日出／日落时刻不合法", component="heliostat")
        if not 0.0 < peak_elevation_deg <= 90.0:
            raise ConfigError("正午最高仰角必须落在 (0, 90] 度", component="heliostat")
        self._sunrise = float(sunrise_minute)
        self._sunset = float(sunset_minute)
        self._peak = float(peak_elevation_deg)

    @property
    def day_length_min(self) -> float:
        return self._sunset - self._sunrise

    def elevation(self, minute_of_day: float) -> float:
        """返回给定时刻的太阳仰角（夜间为负）。"""

        moment = float(minute_of_day)
        if moment <= self._sunrise or moment >= self._sunset:
            return -5.0
        fraction = (moment - self._sunrise) / self.day_length_min
        return self._peak * math.sin(math.pi * fraction)

    def azimuth(self, minute_of_day: float) -> float:
        """返回给定时刻的太阳方位角（自正北顺时针，正午在南）。"""

        moment = float(minute_of_day)
        if moment <= self._sunrise:
            return 90.0
        if moment >= self._sunset:
            return 270.0
        fraction = (moment - self._sunrise) / self.day_length_min
        return 90.0 + 180.0 * fraction

    def at(self, minute_of_day: float) -> SunPosition:
        """返回某一时刻的完整太阳位置。"""

        return SunPosition(
            minute_of_day=float(minute_of_day),
            azimuth_deg=self.azimuth(minute_of_day),
            elevation_deg=self.elevation(minute_of_day),
        )

    def noon(self) -> SunPosition:
        """返回正午位置，供自检与标定使用。"""

        return self.at(self._sunrise + self.day_length_min / 2.0)
