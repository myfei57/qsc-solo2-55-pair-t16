"""手动接管租约。

操作员在现场手动对齐一面镜子时，自动追日不能接着把它转走。租约带有效期：
到期自动失效，避免"忘了释放"把镜子永久钉在手动姿态。租约只登记"谁、哪台、
什么角度、到什么时候"，自动追日是否让路由编排按 ``active`` 判定。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import NamespaceError


@dataclass(frozen=True)
class ManualLease:
    """一份手动接管租约。"""

    mirror: str
    operator: str
    azimuth_deg: float
    elevation_deg: float
    granted_at: float
    expires_at: float

    def holds(self, now: float) -> bool:
        """租约在给定时刻是否仍然有效。"""

        return float(now) < self.expires_at

    def as_dict(self, now: float | None = None) -> dict:
        moment = self.expires_at if now is None else float(now)
        return {
            "mirror": self.mirror,
            "operator": self.operator,
            "azimuth_deg": round(self.azimuth_deg, 3),
            "elevation_deg": round(self.elevation_deg, 3),
            "granted_at": round(self.granted_at, 3),
            "expires_at": round(self.expires_at, 3),
            "remaining_s": round(max(0.0, self.expires_at - moment), 3),
        }


class ManualOverride:
    """手动接管租约台账。"""

    def __init__(self, default_hold_s: float = 900.0) -> None:
        if default_hold_s <= 0:
            raise NamespaceError("租约默认有效期必须为正", component="field")
        self._default_hold_s = float(default_hold_s)
        self._leases: dict[str, ManualLease] = {}
        self._expired = 0

    @property
    def default_hold_s(self) -> float:
        return self._default_hold_s

    def request(
        self,
        mirror: str,
        azimuth_deg: float,
        elevation_deg: float,
        operator: str,
        now: float,
        hold_s: float | None = None,
    ) -> ManualLease:
        """登记一份手动租约（同一台镜子的旧租约被替换）。"""

        if not mirror:
            raise NamespaceError("手动接管必须指定镜子", component="field")
        if not operator:
            raise NamespaceError("手动接管必须记录操作人", component="field")
        span = self._default_hold_s if hold_s is None else float(hold_s)
        if span <= 0:
            raise NamespaceError("租约有效期必须为正", component="field")
        lease = ManualLease(
            mirror=str(mirror),
            operator=str(operator),
            azimuth_deg=float(azimuth_deg) % 360.0,
            elevation_deg=float(elevation_deg),
            granted_at=float(now),
            expires_at=float(now) + span,
        )
        self._leases[lease.mirror] = lease
        return lease

    def get(self, mirror: str) -> ManualLease | None:
        """取某台镜子的租约。"""

        return self._leases.get(mirror)

    def active(self, mirror: str, now: float) -> bool:
        """某台镜子在给定时刻是否处于手动接管。"""

        lease = self._leases.get(mirror)
        return lease is not None and lease.holds(now)

    def expire(self, now: float) -> list[str]:
        """清理过期租约，返回被清掉的镜子名。"""

        gone = [name for name, lease in self._leases.items() if not lease.holds(now)]
        for name in gone:
            del self._leases[name]
        self._expired += len(gone)
        return sorted(gone)

    def release(self, mirror: str) -> bool:
        """释放指定租约，返回是否真的释放了。"""

        return self._leases.pop(str(mirror), None) is not None

    def clear(self) -> int:
        """清空全部租约，返回被清掉的数量（故障闩锁时使用）。"""

        count = len(self._leases)
        self._leases.clear()
        return count

    def held(self) -> list[ManualLease]:
        """返回当前登记的全部租约（按镜子名排序）。"""

        return [self._leases[name] for name in sorted(self._leases)]

    def count(self) -> int:
        """返回当前有效租约数量。"""

        return len(self._leases)

    def expired(self) -> int:
        """返回累计因过期被清理的租约数量。"""

        return self._expired
