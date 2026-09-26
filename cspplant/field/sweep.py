"""清洗与检修窗口。

清洗按批进行，一批镜子对应一个避让区。窗口打开时这些镜子退出聚焦并进入
避让姿态，窗口关闭时再放回。窗口是"一批镜子 + 一个区"的绑定关系，批次一变
避让范围就必须跟着重算——这正是现场最容易漏掉的一步。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import FieldLimits
from ..errors import InterlockError


@dataclass(frozen=True)
class SweepWindow:
    """一次清洗／检修窗口。"""

    mirrors: tuple[str, ...]
    zone: str
    opened_at: float
    closed_at: float | None = None

    @property
    def open(self) -> bool:
        return self.closed_at is None

    def as_dict(self) -> dict:
        return {
            "mirrors": list(self.mirrors),
            "zone": self.zone,
            "opened_at": round(self.opened_at, 3),
            "closed_at": None if self.closed_at is None else round(self.closed_at, 3),
            "open": self.open,
        }


@dataclass
class _Ledger:
    """内部账本：窗口历史与当前窗口。"""

    windows: list[SweepWindow] = field(default_factory=list)
    active: SweepWindow | None = None


class SweepPlanner:
    """按批次切分清洗名单，并维护当前窗口。"""

    def __init__(self, limits: FieldLimits) -> None:
        self._limits = limits
        self._ledger = _Ledger()

    @property
    def limits(self) -> FieldLimits:
        return self._limits

    def plan(self, names: list[str]) -> list[list[str]]:
        """把名单按批大小切开，返回每批的镜子名。"""

        ordered = sorted(dict.fromkeys(str(name) for name in names))
        size = self._limits.sweep_batch
        return [ordered[index : index + size] for index in range(0, len(ordered), size)]

    def begin(self, mirrors: list[str], zone: str, opened_at: float) -> SweepWindow:
        """打开一个清洗窗口；已有窗口未关闭即拒绝。"""

        if self._ledger.active is not None:
            raise InterlockError(
                f"清洗窗口 {self._ledger.active.zone} 尚未关闭", component="field"
            )
        if not mirrors:
            raise InterlockError("清洗窗口不能是空批次", component="field")
        if not zone:
            raise InterlockError("清洗窗口必须绑定避让区", component="field")
        window = SweepWindow(tuple(sorted(dict.fromkeys(mirrors))), str(zone), float(opened_at))
        self._ledger.active = window
        self._ledger.windows.append(window)
        return window

    def finish(self, closed_at: float) -> SweepWindow:
        """关闭当前窗口，返回关闭后的窗口记录。"""

        active = self._ledger.active
        if active is None:
            raise InterlockError("当前没有打开的清洗窗口", component="field")
        closed = SweepWindow(active.mirrors, active.zone, active.opened_at, float(closed_at))
        self._ledger.windows[-1] = closed
        self._ledger.active = None
        return closed

    def active(self) -> SweepWindow | None:
        """返回当前窗口（没有就返回 ``None``）。"""

        return self._ledger.active

    def active_mirrors(self) -> list[str]:
        """返回当前窗口里的镜子名。"""

        active = self._ledger.active
        return [] if active is None else list(active.mirrors)

    def windows(self) -> list[SweepWindow]:
        """返回全部窗口记录。"""

        return list(self._ledger.windows)

    def swept_mirrors(self) -> list[str]:
        """返回历史上进过窗口的全部镜子（去重排序）。"""

        seen: set[str] = set()
        for window in self._ledger.windows:
            seen.update(window.mirrors)
        return sorted(seen)

    def zones_used(self) -> list[str]:
        """返回窗口绑过的避让区（去重排序）。"""

        return sorted({window.zone for window in self._ledger.windows})
