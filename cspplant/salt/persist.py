"""盐侧持久化。

冷热罐液位与流量基线跨重启必须保留：重启后如果液位回到初值，操作员看到的
存量就是假的；基线丢失则联锁门限回到出厂值，低流量保护会误动。
"""

from __future__ import annotations

from ..store.jsonfile import JsonStore

LEVELS_DOC = "salt/levels"
BASELINE_DOC = "salt/baseline"


class SaltPersistence:
    """盐侧状态读写。"""

    def __init__(self, store: JsonStore) -> None:
        self._store = store

    @property
    def store(self) -> JsonStore:
        return self._store

    def save_levels(self, cold_pct: float, hot_pct: float, ts: float) -> str:
        """落盘冷热罐液位。"""

        return self._store.write(
            LEVELS_DOC,
            {
                "cold_pct": round(float(cold_pct), 4),
                "hot_pct": round(float(hot_pct), 4),
                "ts": round(float(ts), 3),
            },
        )

    def read_levels(self) -> dict | None:
        """读回冷热罐液位。"""

        raw = self._store.read(LEVELS_DOC)
        if not isinstance(raw, dict):
            return None
        cold = raw.get("cold_pct")
        hot = raw.get("hot_pct")
        if not isinstance(cold, (int, float)) or not isinstance(hot, (int, float)):
            return None
        return {"cold_pct": float(cold), "hot_pct": float(hot), "ts": float(raw.get("ts", 0.0))}

    def save_baseline(self, baseline_kg_s: float, epoch: int) -> str:
        """落盘流量基线与代际。"""

        return self._store.write(
            BASELINE_DOC,
            {"baseline_kg_s": round(float(baseline_kg_s), 4), "epoch": int(epoch)},
        )

    def read_baseline(self) -> dict | None:
        """读回流量基线。"""

        raw = self._store.read(BASELINE_DOC)
        if not isinstance(raw, dict):
            return None
        value = raw.get("baseline_kg_s")
        if not isinstance(value, (int, float)):
            return None
        return {"baseline_kg_s": float(value), "epoch": int(raw.get("epoch", 0))}

    def documents(self) -> list[str]:
        """返回盐侧已落盘的文档列表。"""

        return [doc for doc in self._store.list_ids() if doc in (LEVELS_DOC, BASELINE_DOC)]
