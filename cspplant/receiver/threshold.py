"""低流量联锁门限。

门限不是一个固定值，而是"当前标定基线 - 容差"。标定动过之后门限必须跟着变，
否则联锁会按旧基线判定，正常波动也会被当成低流量。这里把基线与代际放在一起，
门限只从基线推导，不允许调用方绕过基线自己给门限。
"""

from __future__ import annotations

from ..config import SaltLimits


class ThresholdModel:
    """按标定基线推导低流量联锁门限。"""

    def __init__(self, limits: SaltLimits, baseline_kg_s: float | None = None) -> None:
        self._limits = limits
        self._baseline = float(limits.flow_baseline_kg_s if baseline_kg_s is None else baseline_kg_s)
        self._epoch = 0
        self._trips = 0
        self._evaluations = 0

    @property
    def limits(self) -> SaltLimits:
        return self._limits

    @property
    def baseline_kg_s(self) -> float:
        return self._baseline

    @property
    def epoch(self) -> int:
        """返回基线代际，标定一次加一。"""

        return self._epoch

    def threshold_kg_s(self) -> float:
        """返回当前联锁门限。"""

        return self._limits.low_flow_threshold(self._baseline)

    def calibrate(self, baseline_kg_s: float) -> float:
        """重新标定基线并推进代际，返回新的基线。"""

        value = float(baseline_kg_s)
        if value <= self._limits.flow_tolerance_kg_s:
            raise ValueError("标定基线必须大于流量容差")
        self._baseline = value
        self._epoch += 1
        return self._baseline

    def evaluate(self, flow_kg_s: float) -> dict:
        """判定一次流量是否低于门限。"""

        self._evaluations += 1
        threshold = self.threshold_kg_s()
        margin = float(flow_kg_s) - threshold
        ok = margin >= 0.0
        if not ok:
            self._trips += 1
        return {
            "flow_kg_s": round(float(flow_kg_s), 3),
            "threshold_kg_s": round(threshold, 3),
            "margin_kg_s": round(margin, 3),
            "ok": ok,
            "baseline_kg_s": round(self._baseline, 3),
            "epoch": self._epoch,
        }

    def trips(self) -> int:
        """返回累计触发次数。"""

        return self._trips

    def evaluations(self) -> int:
        """返回累计判定次数。"""

        return self._evaluations
