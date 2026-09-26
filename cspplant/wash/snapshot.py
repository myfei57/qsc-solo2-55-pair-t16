"""清洗状态快照。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WashSnapshot:
    """清洗系统某一时刻的状态。"""

    state: str
    mirror: str
    progress: float
    water_m3: float
    tray_m3: float
    return_pump_running: bool
    washes: int
    cycles: int
    overflow_risks: int

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "mirror": self.mirror,
            "progress": round(self.progress, 3),
            "water_m3": round(self.water_m3, 4),
            "tray_m3": round(self.tray_m3, 4),
            "return_pump_running": self.return_pump_running,
            "washes": self.washes,
            "cycles": self.cycles,
            "overflow_risks": self.overflow_risks,
        }
