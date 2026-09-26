"""环境温度模型。

环境温度按日内的正弦走势给出：凌晨最低、午后最高。散热项与降额余量都按它
计算，因此同一个出口温度在不同季节对应的余量并不相同。
"""

from __future__ import annotations

import math


class AmbientModel:
    """环境温度的解析曲线。"""

    def __init__(self, min_c: float = 8.0, max_c: float = 34.0, peak_minute: float = 900.0) -> None:
        if max_c <= min_c:
            raise ValueError("最高环境温度必须高于最低环境温度")
        self._min = float(min_c)
        self._max = float(max_c)
        self._peak_minute = float(peak_minute) % 1440.0

    @property
    def min_c(self) -> float:
        return self._min

    @property
    def max_c(self) -> float:
        return self._max

    def at(self, minute_of_day: float) -> float:
        """返回给定时刻的环境温度。"""

        moment = float(minute_of_day) % 1440.0
        offset = (moment - self._peak_minute) / 1440.0 * 2.0 * math.pi
        mid = (self._max + self._min) / 2.0
        amplitude = (self._max - self._min) / 2.0
        return mid + amplitude * math.cos(offset)

    def mean_c(self) -> float:
        """返回日均环境温度。"""

        return (self._max + self._min) / 2.0
