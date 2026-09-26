"""熔盐回路。

冷盐罐、热盐罐、两台盐泵与母管组成循环：冷泵把冷盐送进吸热器，热盐回到热罐，
负荷变化时按需切换。切换必须"先停后启"——两台泵同时在运会在母管上对顶，
压力顶到保护值。流量基线由标定给出，联锁门限从基线推导。
"""

from __future__ import annotations

from .calibrate import CalibrationRecord, FlowCalibrator
from .handoff import HandoffReport, TankHandoff
from .hydraulic import HydraulicLoop
from .loop import SaltLoop
from .persist import SaltPersistence
from .pump import SaltPump
from .snapshot import SaltSnapshot
from .tank import SaltTank

__all__ = [
    "CalibrationRecord",
    "FlowCalibrator",
    "HandoffReport",
    "TankHandoff",
    "HydraulicLoop",
    "SaltLoop",
    "SaltPersistence",
    "SaltPump",
    "SaltSnapshot",
    "SaltTank",
]
