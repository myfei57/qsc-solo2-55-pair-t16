"""现场命名空间与工艺链拓扑。

命名空间把 ``区域.设备`` 映射到设备类型，控制台与审计共用同一套名字；拓扑
描述「辐照采集 → 镜场追日 → 吸热器受热 → 熔盐循环 → 蒸汽 → 审计」这条硬
顺序链，用来在操作前检查工位是否满足前一阶段已完成。
"""

from __future__ import annotations

from .registry import Device, NamespaceRegistry
from .topology import STAGES, Stage, Topology, build_default_topology
from .zones import SafeZone, ZoneRegistry

__all__ = [
    "Device",
    "NamespaceRegistry",
    "STAGES",
    "Stage",
    "Topology",
    "build_default_topology",
    "SafeZone",
    "ZoneRegistry",
]
