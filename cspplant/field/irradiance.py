"""辐照采集与落盘。

辐照计给出的是瞬时 DNI，追日用的却是**已落盘**的快照：现场重启后控制链只能
从盘上恢复，如果追日读的是内存里那份还没落盘的值，重启后就会按旧快照对焦，
吸热器局部超温。因此这里把"当前值"和"已落盘值"分成两份状态，并显式暴露两者
是否一致，让编排无法绕过落盘这一步。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import IrradianceLimits
from .persist import FieldPersistence


@dataclass(frozen=True)
class IrradianceSample:
    """一次辐照采集结果。"""

    accepted: bool
    dni_w_m2: float
    ts: float
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "dni_w_m2": round(self.dni_w_m2, 2),
            "ts": round(self.ts, 3),
            "reason": self.reason,
        }


class IrradianceMeter:
    """辐照计与它的落盘副本。"""

    def __init__(self, limits: IrradianceLimits, persistence: FieldPersistence) -> None:
        self._limits = limits
        self._persistence = persistence
        self._current = 0.0
        self._sampled_at = 0.0
        self._samples = 0
        self._rejected = 0
        self._persists = 0
        self._restore()

    @property
    def limits(self) -> IrradianceLimits:
        return self._limits

    @property
    def persistence(self) -> FieldPersistence:
        return self._persistence

    def _restore(self) -> None:
        """重启后从盘上恢复当前值，避免"内存空、盘上有"的错位。"""

        stored = self._persistence.read_irradiance()
        if stored is None:
            return
        self._current = stored["dni_w_m2"]
        self._sampled_at = stored["ts"]

    def sample(self, dni_w_m2: float, ts: float) -> IrradianceSample:
        """采集一次 DNI；越界读数被拒绝并保留上一次可信值。"""

        value = float(dni_w_m2)
        if not self._limits.in_range(value):
            self._rejected += 1
            return IrradianceSample(False, self._current, float(ts), reason="out_of_range")
        self._current = value
        self._sampled_at = float(ts)
        self._samples += 1
        return IrradianceSample(True, value, float(ts))

    def current(self) -> float:
        """返回最近一次采集到的 DNI。"""

        return self._current

    def sampled_at(self) -> float:
        """返回最近一次采集时刻。"""

        return self._sampled_at

    def persist(self) -> float:
        """把当前采集值落盘，返回落盘值。"""

        self._persistence.write_irradiance(self._current, self._sampled_at)
        self._persists += 1
        return self._current

    def durable(self) -> float:
        """读回**已落盘**的 DNI；盘上没有就返回 0，调用方必须据此拒绝追日。"""

        stored = self._persistence.read_irradiance()
        return 0.0 if stored is None else stored["dni_w_m2"]

    def durable_at(self) -> float:
        """返回已落盘值的采集时刻（没有落盘时返回 0）。"""

        stored = self._persistence.read_irradiance()
        return 0.0 if stored is None else stored["ts"]

    def durable_matches_current(self) -> bool:
        """盘上快照是否就是当前采集值。"""

        stored = self._persistence.read_irradiance()
        if stored is None:
            return False
        return abs(stored["dni_w_m2"] - self._current) < 1e-9

    def stale(self, now: float) -> bool:
        """已落盘快照是否已经过期。"""

        stored = self._persistence.read_irradiance()
        if stored is None:
            return True
        return (float(now) - stored["ts"]) > self._limits.stale_after_s

    def sample_count(self) -> int:
        """返回被接受的采样次数。"""

        return self._samples

    def rejected(self) -> int:
        """返回被拒绝的越界读数次数。"""

        return self._rejected

    def persist_count(self) -> int:
        """返回落盘次数。"""

        return self._persists
