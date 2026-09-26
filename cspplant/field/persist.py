"""镜场持久化。

镜场有两类需要跨重启保留的状态：辐照度快照（追日必须按它来，不能用内存里
可能更新却没落盘的值）与清洗／检修窗口（重启后仍在窗口里的镜子要继续避让）。
两者都走 :class:`~cspplant.store.jsonfile.JsonStore`，文档路径固定，读取失败
按"文件不存在"处理。
"""

from __future__ import annotations

from ..store.jsonfile import JsonStore

IRRADIANCE_DOC = "field/irradiance"
WINDOW_DOC = "field/window"


class FieldPersistence:
    """镜场状态的读写入口。"""

    def __init__(self, store: JsonStore) -> None:
        self._store = store

    @property
    def store(self) -> JsonStore:
        return self._store

    def write_irradiance(self, dni_w_m2: float, ts: float) -> str:
        """落盘一次辐照度快照，返回文件路径。"""

        return self._store.write(
            IRRADIANCE_DOC,
            {"dni_w_m2": round(float(dni_w_m2), 3), "ts": round(float(ts), 3)},
        )

    def read_irradiance(self) -> dict | None:
        """读回落盘的辐照度快照，没有就返回 ``None``。"""

        raw = self._store.read(IRRADIANCE_DOC)
        if not isinstance(raw, dict):
            return None
        dni = raw.get("dni_w_m2")
        if not isinstance(dni, (int, float)):
            return None
        return {"dni_w_m2": float(dni), "ts": float(raw.get("ts", 0.0))}

    def write_window(self, mirrors: list[str], zone: str, opened_at: float) -> str:
        """落盘当前清洗／检修窗口。"""

        return self._store.write(
            WINDOW_DOC,
            {
                "mirrors": list(mirrors),
                "zone": zone,
                "opened_at": round(float(opened_at), 3),
            },
        )

    def read_window(self) -> dict | None:
        """读回清洗／检修窗口。"""

        raw = self._store.read(WINDOW_DOC)
        if not isinstance(raw, dict):
            return None
        mirrors = raw.get("mirrors")
        if not isinstance(mirrors, list):
            return None
        return {
            "mirrors": [str(item) for item in mirrors],
            "zone": str(raw.get("zone", "")),
            "opened_at": float(raw.get("opened_at", 0.0)),
        }

    def clear_window(self) -> bool:
        """删除窗口文档，返回是否真的删掉了。"""

        return self._store.remove(WINDOW_DOC)

    def documents(self) -> list[str]:
        """返回镜场已经落盘的文档列表。"""

        return [
            doc
            for doc in self._store.list_ids()
            if doc in (IRRADIANCE_DOC, WINDOW_DOC)
        ]
