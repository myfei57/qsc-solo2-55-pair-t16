"""追日目标角度。

反射定律要求镜面法线平分"入射方向"与"出射方向"：入射来自太阳，出射指向
吸热器窗口。因此目标仰角取两者仰角的平均，目标方位角取两者方位角的平均
（跨 0/360 度时按最短弧计算）。控制链只下发目标角度，具体怎么转由
``heliostat.drive`` 按转速上限推进。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .sun import SunPosition


@dataclass(frozen=True)
class AimPoint:
    """吸热器窗口上的瞄准点（场地坐标系，米）。"""

    x_m: float
    y_m: float
    z_m: float

    def as_dict(self) -> dict:
        return {"x_m": round(self.x_m, 2), "y_m": round(self.y_m, 2), "z_m": round(self.z_m, 2)}


@dataclass(frozen=True)
class AimAngles:
    """一对目标角度。"""

    azimuth_deg: float
    elevation_deg: float

    def as_dict(self) -> dict:
        return {"azimuth_deg": round(self.azimuth_deg, 3), "elevation_deg": round(self.elevation_deg, 3)}


def normalize_azimuth(angle_deg: float) -> float:
    """把方位角归一化到 [0, 360)。"""

    return float(angle_deg) % 360.0


def shortest_azimuth_delta(from_deg: float, to_deg: float) -> float:
    """返回从 ``from_deg`` 转到 ``to_deg`` 的最短有向角度差。"""

    return (normalize_azimuth(to_deg) - normalize_azimuth(from_deg) + 180.0) % 360.0 - 180.0


def angle_error(current: AimAngles, target: AimAngles) -> AimAngles:
    """返回当前角度与目标角度的误差（方位取最短弧）。"""

    return AimAngles(
        azimuth_deg=shortest_azimuth_delta(current.azimuth_deg, target.azimuth_deg),
        elevation_deg=target.elevation_deg - current.elevation_deg,
    )


def aim_angles_for(
    mirror_x_m: float,
    mirror_y_m: float,
    mirror_z_m: float,
    sun: SunPosition,
    point: AimPoint,
) -> AimAngles:
    """按反射定律计算某台定日镜的目标角度。"""

    dx = point.x_m - mirror_x_m
    dy = point.y_m - mirror_y_m
    dz = point.z_m - mirror_z_m
    ground = math.hypot(dx, dy)
    if ground <= 0.0:
        raise ValueError("定日镜不能与瞄准点重合")
    target_azimuth = math.degrees(math.atan2(dx, dy)) % 360.0
    target_elevation = math.degrees(math.atan2(dz, ground))
    return AimAngles(
        azimuth_deg=normalize_azimuth((sun.azimuth_deg + target_azimuth) / 2.0),
        elevation_deg=(sun.elevation_deg + target_elevation) / 2.0,
    )


def stow_angles(stow_elevation_deg: float, azimuth_deg: float = 180.0) -> AimAngles:
    """返回收起姿态：镜面朝下，避免把光打到任何工位。"""

    return AimAngles(azimuth_deg=normalize_azimuth(azimuth_deg), elevation_deg=float(stow_elevation_deg))
