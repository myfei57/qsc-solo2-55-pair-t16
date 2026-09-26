"""镜面清洗。

清洗车按"到镜 → 喷淋 → 回水回收 → 排水"跑一个循环。回水回收这条链最容易
出错：排水没排完就把回水泵停掉，回水会溢到地面并流进电气设备。因此顺序被
写死成"先开排水阀、排空确认之后才停回水泵"。
"""

from __future__ import annotations

from .cycle import WashCycle, WashReport
from .drain import DrainSystem
from .snapshot import WashSnapshot
from .truck import WashTruck

__all__ = ["WashCycle", "WashReport", "DrainSystem", "WashSnapshot", "WashTruck"]
