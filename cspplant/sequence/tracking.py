"""追日编排：采集 → 落盘 → 判定 → 下发。

落盘不是可选项：重启后控制链只能从盘上恢复辐照度，如果追日在落盘之前就用了
内存里的当前值，重启后镜场会按旧快照对焦。这里把顺序写死，落盘失败或落盘值
与当前值不一致都不允许进入聚焦。
"""

from __future__ import annotations

from ..field.field import FieldController
from ..heliostat.sun import SunPosition


def acquire_and_track(
    field: FieldController,
    dni_w_m2: float,
    sun: SunPosition,
    now: float,
    latched: bool = False,
) -> dict:
    """采集一次辐照、落盘，再按落盘值追日。"""

    steps: list[dict] = []
    violations: list[str] = []

    sample = field.acquire(dni_w_m2, now)
    steps.append({"component": "field", "action": "acquire", "detail": sample.as_dict()})
    if not sample.accepted:
        violations.append("irradiance_rejected")
        return {"ok": False, "steps": steps, "violations": violations, "aimed": 0}

    durable = field.persist_irradiance()
    steps.append(
        {
            "component": "field",
            "action": "persist_irradiance",
            "detail": {"dni_w_m2": round(durable, 2), "matches_current": field.meter.durable_matches_current()},
        }
    )
    if durable <= 0.0:
        violations.append("irradiance_not_persisted")
        return {"ok": False, "steps": steps, "violations": violations, "aimed": 0}

    report = field.track(sun, now, latched)
    steps.extend(report["steps"])
    violations.extend(report["violations"])
    return {
        "ok": report["ok"] and not violations,
        "mode": report.get("mode", ""),
        "reason": report.get("reason", ""),
        "steps": steps,
        "violations": violations,
        "aimed": report.get("aimed", 0),
    }
