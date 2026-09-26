"""定日镜状态快照。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AimSnapshot:
    """一对角度及其误差。"""

    azimuth_deg: float
    elevation_deg: float
    azimuth_error_deg: float
    elevation_error_deg: float
    within_tolerance: bool

    def as_dict(self) -> dict:
        return {
            "azimuth_deg": round(self.azimuth_deg, 3),
            "elevation_deg": round(self.elevation_deg, 3),
            "azimuth_error_deg": round(self.azimuth_error_deg, 4),
            "elevation_error_deg": round(self.elevation_error_deg, 4),
            "within_tolerance": self.within_tolerance,
        }


@dataclass(frozen=True)
class HeliostatSnapshot:
    """一台定日镜某一时刻的状态。"""

    name: str
    state: str
    manual: bool
    excluded: bool
    azimuth_deg: float
    elevation_deg: float
    target_azimuth_deg: float
    target_elevation_deg: float
    turns: int

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "manual": self.manual,
            "excluded": self.excluded,
            "azimuth_deg": round(self.azimuth_deg, 3),
            "elevation_deg": round(self.elevation_deg, 3),
            "target_azimuth_deg": round(self.target_azimuth_deg, 3),
            "target_elevation_deg": round(self.target_elevation_deg, 3),
            "turns": self.turns,
        }
