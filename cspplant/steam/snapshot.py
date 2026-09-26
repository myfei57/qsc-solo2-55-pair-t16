"""蒸汽系统状态快照。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SteamSnapshot:
    """蒸汽系统某一时刻的状态。"""

    pressure_mpa: float
    settled: bool
    bypass_open: bool
    main_stop_open: bool
    load_mw: float
    target_mw: float
    production_kg_s: float
    sheds: int

    def as_dict(self) -> dict:
        return {
            "pressure_mpa": round(self.pressure_mpa, 3),
            "settled": self.settled,
            "bypass_open": self.bypass_open,
            "main_stop_open": self.main_stop_open,
            "load_mw": round(self.load_mw, 3),
            "target_mw": round(self.target_mw, 3),
            "production_kg_s": round(self.production_kg_s, 3),
            "sheds": self.sheds,
        }
