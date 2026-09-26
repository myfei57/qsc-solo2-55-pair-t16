"""定日镜。

一台定日镜只有两件事：把镜面转到让反射光落在吸热器窗口的角度，以及在检修
或清洗时退到不朝聚光方向的安全姿态。转动受最大角速度限制，姿态判定按方位
与仰角两个自由度分别核对，避免"转过去了但没转够"被当成完成。
"""

from __future__ import annotations

from .aim import AimAngles, AimPoint, aim_angles_for, angle_error, stow_angles
from .drive import DEFOCUS, IDLE, MANUAL, STOWED, TRACKING, HeliostatDrive
from .registry import Heliostat, HeliostatRegistry
from .snapshot import AimSnapshot, HeliostatSnapshot
from .sun import SunModel, SunPosition
from .zone import ZoneGuard, ZoneVerdict

__all__ = [
    "AimAngles",
    "AimPoint",
    "aim_angles_for",
    "angle_error",
    "stow_angles",
    "HeliostatDrive",
    "IDLE",
    "TRACKING",
    "DEFOCUS",
    "STOWED",
    "MANUAL",
    "Heliostat",
    "HeliostatRegistry",
    "AimSnapshot",
    "HeliostatSnapshot",
    "SunModel",
    "SunPosition",
    "ZoneGuard",
    "ZoneVerdict",
]
