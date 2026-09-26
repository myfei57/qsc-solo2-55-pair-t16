"""镜面清洗编排：开窗口 → 重算避让区 → 逐面清洗 → 关窗口。

窗口一开，批次里的镜子就退出聚焦；避让区必须按新批次重新求值，否则镜子会停
在朝着工位检修区的姿态上。清洗本身走回水回收顺序，最后才关闭窗口把镜子放回
自动追日。
"""

from __future__ import annotations

from ..field.field import FieldController
from ..wash.cycle import WashCycle
from ..wash.drain import DrainSystem
from ..wash.truck import WashTruck


def wash_mirrors(
    field: FieldController,
    cycle: WashCycle,
    truck: WashTruck,
    drain: DrainSystem,
    mirrors: list[str],
    zone: str,
    now: float,
    dt: float = 1.0,
) -> dict:
    """按批次清洗镜面，返回带步骤与违约项的报告。"""

    steps: list[dict] = []
    violations: list[str] = []

    window = field.open_window(mirrors, zone, now)
    steps.append(
        {
            "component": "field",
            "action": "open_window",
            "detail": {"zone": window.zone, "mirrors": list(window.mirrors)},
        }
    )

    refreshed = field.refresh_zones(now)
    steps.append(
        {
            "component": "field",
            "action": "refresh_zones",
            "detail": {"refreshed": len(refreshed), "postures": {item["mirror"]: item["posture"] for item in refreshed}},
        }
    )
    if len(refreshed) != len(window.mirrors):
        violations.append("zone_refresh_incomplete")

    washed = 0
    for mirror in window.mirrors:
        report = cycle.run(truck, drain, mirror, dt)
        steps.extend(report.steps)
        violations.extend(report.violations)
        if report.ok:
            washed += 1

    closed = field.close_window(now)
    steps.append(
        {
            "component": "field",
            "action": "close_window",
            "detail": {"zone": closed.zone, "closed_at": closed.closed_at},
        }
    )

    return {
        "ok": not violations,
        "steps": steps,
        "violations": violations,
        "washed": washed,
        "mirrors": len(window.mirrors),
    }
