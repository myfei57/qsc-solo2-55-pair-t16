"""晴天辐照曲线。

台面辐照的日走势按正弦近似：日出为零、正午最高、日落归零。曲线只用于预估与
排程，实时保护仍以辐照计采样为准；但它给出了"今天还剩多少可用的辐照"，是
决定要不要开始清洗或切换盐罐的依据。
"""

from __future__ import annotations

import math


class ClearSkyProfile:
    """晴天辐照的解析曲线。"""

    def __init__(
        self,
        peak_dni_w_m2: float = 950.0,
        sunrise_minute: float = 360.0,
        sunset_minute: float = 1080.0,
    ) -> None:
        if peak_dni_w_m2 <= 0:
            raise ValueError("峰值辐照必须为正")
        if not 0.0 <= sunrise_minute < sunset_minute <= 1440.0:
            raise ValueError("日出／日落时刻不合法")
        self._peak = float(peak_dni_w_m2)
        self._sunrise = float(sunrise_minute)
        self._sunset = float(sunset_minute)

    @property
    def peak_dni_w_m2(self) -> float:
        return self._peak

    @property
    def day_length_min(self) -> float:
        return self._sunset - self._sunrise

    def dni_w_m2(self, minute_of_day: float, cloud_factor: float = 1.0) -> float:
        """返回给定时刻的辐照度；``cloud_factor`` 用于模拟云量衰减。"""

        moment = float(minute_of_day)
        if moment <= self._sunrise or moment >= self._sunset:
            return 0.0
        fraction = (moment - self._sunrise) / self.day_length_min
        factor = max(0.0, min(1.0, float(cloud_factor)))
        return self._peak * math.sin(math.pi * fraction) * factor

    def energy_remaining_kj_m2(self, minute_of_day: float, cloud_factor: float = 1.0) -> float:
        """返回从当前时刻到日落还能接到的辐照量（kJ/m²）。"""

        moment = max(self._sunrise, min(self._sunset, float(minute_of_day)))
        steps = 60
        span = (self._sunset - moment) / steps
        total = 0.0
        for index in range(steps):
            total += self.dni_w_m2(moment + span * index, cloud_factor) * span * 60.0 / 1000.0
        return total

    def peak_minute(self) -> float:
        """返回辐照峰值出现的时刻。"""

        return self._sunrise + self.day_length_min / 2.0
