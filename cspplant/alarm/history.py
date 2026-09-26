"""告警历史。

告警保留最近若干条，既能给控制台"最近告警"列表，也能按规则名统计窗口内的
出现次数供升级使用。历史上限固定，长时间运行不会把内存撑爆。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .rules import AlarmEvent


@dataclass(frozen=True)
class AlarmEntry:
    """一条带时间戳的告警。"""

    ts: float
    event: AlarmEvent

    def as_dict(self) -> dict:
        return {"ts": round(self.ts, 3), **self.event.as_dict()}


class AlarmHistory:
    """告警历史环形缓冲。"""

    def __init__(self, limit: int = 1024) -> None:
        self._limit = max(1, int(limit))
        self._entries: list[AlarmEntry] = []
        self._lock = threading.RLock()

    def record(self, ts: float, event: AlarmEvent) -> dict:
        """记录一条告警，返回其字典形式。"""

        entry = AlarmEntry(float(ts), event)
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._limit:
                del self._entries[: len(self._entries) - self._limit]
        return entry.as_dict()

    def record_all(self, ts: float, events: list[AlarmEvent]) -> list[dict]:
        """批量记录本轮告警，返回落库结果。"""

        return [self.record(ts, event) for event in events]

    def recent(self, limit: int = 20) -> list[dict]:
        """返回最近的告警（最新的在前）。"""

        with self._lock:
            selected = list(self._entries)[-limit:]
        return [entry.as_dict() for entry in reversed(selected)]

    def occurrences(self, rule: str, since: float) -> int:
        """统计某规则在给定时刻之后出现的次数。"""

        with self._lock:
            return len([entry for entry in self._entries if entry.event.rule == rule and entry.ts >= since])

    def by_rule(self) -> dict:
        """按规则名汇总出现次数。"""

        with self._lock:
            entries = list(self._entries)
        counts: dict[str, int] = {}
        for entry in entries:
            counts[entry.event.rule] = counts.get(entry.event.rule, 0) + 1
        return counts

    def count(self) -> int:
        """返回历史条数。"""

        with self._lock:
            return len(self._entries)
