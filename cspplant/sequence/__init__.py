"""跨组件操作编排。

每个操作都是真实的跨模块调用链，顺序在编排里写死：

- 追日：采集 → **落盘** → 判定策略 → 下发目标；
- 熔盐循环：启泵 → **流量确认** → 通盐 → 带负荷；
- 停堆保护：过温 → 闩锁 → 镜场收起 → 汽机走旁路 → 吸热器跳闸；
- 镜面清洗：开窗口 → 避让区重算 → 喷淋 → 排空后停回水泵。

顺序被破坏时报告里的 ``violations`` 会点出来，由平台写审计。
"""

from __future__ import annotations

from .circulation import circulate
from .startup import shutdown_plant, startup_plant
from .tracking import acquire_and_track
from .trip import emergency_trip, reset_after_trip
from .washing import wash_mirrors

__all__ = [
    "circulate",
    "shutdown_plant",
    "startup_plant",
    "acquire_and_track",
    "emergency_trip",
    "reset_after_trip",
    "wash_mirrors",
]
