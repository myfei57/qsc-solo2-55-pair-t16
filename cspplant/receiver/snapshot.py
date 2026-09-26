"""吸热器状态快照。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReceiverSnapshot:
    """吸热器某一时刻的状态。"""

    state: str
    temperature_c: float
    peak_c: float
    valve_pct: float
    flow_kg_s: float
    flow_confirmed: bool
    hold_s: float
    derated: bool
    ceiling_pct: float
    trip_reason: str

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "temperature_c": round(self.temperature_c, 2),
            "peak_c": round(self.peak_c, 2),
            "valve_pct": round(self.valve_pct, 2),
            "flow_kg_s": round(self.flow_kg_s, 3),
            "flow_confirmed": self.flow_confirmed,
            "hold_s": round(self.hold_s, 3),
            "derated": self.derated,
            "ceiling_pct": round(self.ceiling_pct, 3),
            "trip_reason": self.trip_reason,
        }
