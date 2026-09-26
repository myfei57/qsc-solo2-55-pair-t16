"""温度与降额。

吸热器出口温度不只是测出来的，还要能提前算出来：晴天曲线给出台面辐照的日
内走势，环境温度模型给出散热条件，温度预估按这两者推未来一段时间的出口温度
与降额余量。控制台靠这份预估决定"现在还能不能把负荷加上去"。
"""

from __future__ import annotations

from .ambient import AmbientModel
from .profile import TemperatureProfile, TemperaturePoint
from .solar import ClearSkyProfile
from .temp import TemperatureReport, build_report

__all__ = [
    "AmbientModel",
    "TemperatureProfile",
    "TemperaturePoint",
    "ClearSkyProfile",
    "TemperatureReport",
    "build_report",
]
