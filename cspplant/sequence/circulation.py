"""熔盐循环编排：启泵 → 流量确认 → 通盐 → 带负荷。

盐泵不能在流量确认之前提转速：流量没建立，低流量联锁就看不到干烧。这里按
"启泵 → 逐步升流 → 累计确认 → 通盐 → 开进口阀"推进，任何一步没成立就停下并
把违约项报出来。
"""

from __future__ import annotations

from ..errors import PlantError
from ..receiver.receiver import Receiver
from ..salt.loop import SaltLoop

MAX_STEPS = 1800


def circulate(
    salt: SaltLoop,
    receiver: Receiver,
    dt: float = 1.0,
    target_flow_kg_s: float | None = None,
) -> dict:
    """建立熔盐循环并把熔盐引进吸热器。"""

    steps: list[dict] = []
    violations: list[str] = []
    target = salt.limits.flow_baseline_kg_s if target_flow_kg_s is None else float(target_flow_kg_s)

    try:
        salt.start_cold()
    except PlantError as error:
        violations.append(error.message)
        return {"ok": False, "steps": steps, "violations": violations, "flow_kg_s": salt.flow_kg_s()}
    steps.append(
        {
            "component": "salt",
            "action": "start_cold_pump",
            "detail": {"target_flow_kg_s": round(target, 3)},
        }
    )

    status = None
    for _ in range(MAX_STEPS):
        salt.step(dt)
        status = receiver.verify_flow(salt.flow_kg_s(), dt)
        if status.confirmed:
            break
    steps.append(
        {
            "component": "receiver",
            "action": "confirm_flow",
            "detail": {
                "flow_kg_s": round(salt.flow_kg_s(), 3),
                "hold_s": round(receiver.gate.hold_s(), 3),
                "confirmed": receiver.flow_confirmed(),
            },
        }
    )
    if not receiver.flow_confirmed():
        violations.append("flow_not_confirmed")
        return {"ok": False, "steps": steps, "violations": violations, "flow_kg_s": salt.flow_kg_s()}

    try:
        receiver.admit()
    except PlantError as error:
        violations.append(error.message)
        return {"ok": False, "steps": steps, "violations": violations, "flow_kg_s": salt.flow_kg_s()}
    steps.append(
        {
            "component": "receiver",
            "action": "admit_salt",
            "detail": {"header": receiver.header.state(), "valve_target_pct": receiver.valve.target_pct()},
        }
    )

    for _ in range(MAX_STEPS):
        receiver.step_valve(dt)
        if receiver.valve.at_target():
            break
    steps.append(
        {
            "component": "receiver",
            "action": "open_inlet_valve",
            "detail": {"position_pct": round(receiver.valve.position_pct(), 2), "at_target": receiver.valve.at_target()},
        }
    )
    if not receiver.valve.at_target():
        violations.append("valve_not_settled")

    return {
        "ok": not violations,
        "steps": steps,
        "violations": violations,
        "flow_kg_s": round(salt.flow_kg_s(), 3),
    }
