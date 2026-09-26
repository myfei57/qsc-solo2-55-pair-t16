"""流量标定。

流量计的读数会随结垢与检修漂移，标定给出新的基线，联锁门限必须立刻按新基线
重算。标定记录保留代际、标定值与操作人，便于巡检比对"哪次标定之后联锁开始
误动"。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import SaltLimits
from ..receiver.threshold import ThresholdModel


@dataclass(frozen=True)
class CalibrationRecord:
    """一次流量标定。"""

    epoch: int
    measured_kg_s: float
    baseline_kg_s: float
    threshold_kg_s: float
    operator: str
    ts: float

    def as_dict(self) -> dict:
        return {
            "epoch": self.epoch,
            "measured_kg_s": round(self.measured_kg_s, 3),
            "baseline_kg_s": round(self.baseline_kg_s, 3),
            "threshold_kg_s": round(self.threshold_kg_s, 3),
            "operator": self.operator,
            "ts": round(self.ts, 3),
        }


class FlowCalibrator:
    """把现场实测流量写回联锁基线。"""

    def __init__(self, limits: SaltLimits, threshold: ThresholdModel) -> None:
        self._limits = limits
        self._threshold = threshold
        self._records: list[CalibrationRecord] = []

    @property
    def limits(self) -> SaltLimits:
        return self._limits

    @property
    def threshold(self) -> ThresholdModel:
        return self._threshold

    def calibrate(self, measured_kg_s: float, now: float, operator: str = "console") -> CalibrationRecord:
        """用实测流量重新标定基线，并同步刷新联锁门限。"""

        measured = float(measured_kg_s)
        if measured <= self._limits.flow_tolerance_kg_s:
            raise ValueError("实测流量必须大于流量容差")
        baseline = self._threshold.calibrate(measured)
        record = CalibrationRecord(
            epoch=self._threshold.epoch,
            measured_kg_s=measured,
            baseline_kg_s=baseline,
            threshold_kg_s=self._threshold.threshold_kg_s(),
            operator=str(operator),
            ts=float(now),
        )
        self._records.append(record)
        return record

    def records(self) -> list[CalibrationRecord]:
        """返回全部标定记录。"""

        return list(self._records)

    def last(self) -> CalibrationRecord | None:
        """返回最近一次标定记录。"""

        return self._records[-1] if self._records else None

    def baseline_kg_s(self) -> float:
        """返回当前基线。"""

        return self._threshold.baseline_kg_s

    def threshold_kg_s(self) -> float:
        """返回当前门限。"""

        return self._threshold.threshold_kg_s()
