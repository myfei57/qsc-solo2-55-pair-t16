"""参数快照与指纹。

快照覆盖生效配置的全部 limits 段落与本次运行参数（DNI、目标负荷、当日分钟），
指纹是规范化 JSON 的 sha256。停机期间有人改过配置、或者恢复时带了不同参数，
指纹就会变，迁移阶段必须重新核对一遍才能续做——不能带着旧假设往下走。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..config import PlatformConfig, config_to_mapping

RUN_PARAM_KEYS = ("dni_w_m2", "load_mw", "minute_of_day")


def run_params(dni_w_m2: float, load_mw: float, minute_of_day: float | None = None) -> dict:
    """整理一次启动的运行参数。"""

    return {
        "dni_w_m2": round(float(dni_w_m2), 3),
        "load_mw": round(float(load_mw), 3),
        "minute_of_day": None if minute_of_day is None else round(float(minute_of_day), 3),
    }


def parameter_snapshot(config: PlatformConfig, params: dict) -> dict:
    """生成参数快照：生效配置 limits + 运行参数。"""

    return {
        "limits": config_to_mapping(config)["limits"],
        "run": {key: params.get(key) for key in RUN_PARAM_KEYS},
    }


def fingerprint(snapshot: dict) -> str:
    """对快照求稳定指纹（键序无关）。"""

    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _flatten(mapping: Any, prefix: str = "") -> dict:
    """把嵌套映射摊平成点分键，供逐项对照。"""

    if not isinstance(mapping, dict):
        return {prefix: mapping}
    flat: dict = {}
    for key, value in mapping.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, dotted))
        else:
            flat[dotted] = value
    return flat


def diff_snapshots(old: dict, new: dict) -> list[dict]:
    """逐项对照两份快照，返回发生变化的键（按键名排序）。"""

    before = _flatten(old)
    after = _flatten(new)
    changed: list[dict] = []
    for key in sorted(set(before) | set(after)):
        was = before.get(key)
        now = after.get(key)
        if was != now:
            changed.append({"key": key, "was": was, "now": now})
    return changed
