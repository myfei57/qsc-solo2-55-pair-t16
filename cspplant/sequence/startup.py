"""启停编排。

启动沿控制链走一遍：辐照采集与落盘 → 镜场追日 → 熔盐循环建立 → 吸热器受热 →
汽机建负荷。停运反过来：先卸载并走旁路，再让镜场收起，最后停泵关母管。任何
一步失败都停在原地，不带着半截状态继续往下走。
"""

from __future__ import annotations

from ..errors import PlantError
from ..field.field import FieldController
from ..heliostat.sun import SunPosition
from ..receiver.receiver import Receiver
from ..salt.loop import SaltLoop
from ..steam.system import SteamSystem
from .circulation import circulate
from .tracking import acquire_and_track

MAX_STEPS = 1800


def startup_plant(
    field: FieldController,
    salt: SaltLoop,
    receiver: Receiver,
    steam: SteamSystem,
    sun: SunPosition,
    dni_w_m2: float,
    now: float,
    load_mw: float,
    dt: float = 1.0,
) -> dict:
    """把整条控制链从冷态带到带负荷。"""

    steps: list[dict] = []
    violations: list[str] = []

    tracking = acquire_and_track(field, dni_w_m2, sun, now)
    steps.extend(tracking["steps"])
    violations.extend(tracking["violations"])
    if not tracking["ok"]:
        return {"ok": False, "steps": steps, "violations": violations, "stage": "tracking"}

    circulation = circulate(salt, receiver, dt)
    steps.extend(circulation["steps"])
    violations.extend(circulation["violations"])
    if not circulation["ok"]:
        return {"ok": False, "steps": steps, "violations": violations, "stage": "circulation"}

    heat = receiver.heat(field.thermal_power_kw(), salt.flow_kg_s(), dt)
    steps.append({"component": "receiver", "action": "first_heat", "detail": heat})
    violations.extend(heat["violations"])
    if not heat["ok"]:
        return {"ok": False, "steps": steps, "violations": violations, "stage": "receiver"}

    load = steam.load(load_mw)
    steps.extend(load.steps)
    violations.extend(load.violations)
    if not load.ok:
        return {"ok": False, "steps": steps, "violations": violations, "stage": "steam"}

    return {
        "ok": not violations,
        "steps": steps,
        "violations": violations,
        "stage": "done",
        "load_mw": round(load.load_mw, 3),
        "flow_kg_s": round(salt.flow_kg_s(), 3),
    }


def shutdown_plant(
    field: FieldController,
    salt: SaltLoop,
    receiver: Receiver,
    steam: SteamSystem,
    now: float,
    dt: float = 1.0,
) -> dict:
    """从带负荷退回停运。"""

    steps: list[dict] = []
    violations: list[str] = []

    shed = steam.shed(0.0, now)
    steps.extend(shed.steps)
    violations.extend(shed.violations)

    stowed = field.stow_all()
    steps.append({"component": "field", "action": "stow_all", "detail": {"mirrors": stowed}})

    if salt.cold_pump.running():
        salt.stop_cold()
    if salt.hot_pump.running():
        salt.stop_hot()
    for _ in range(MAX_STEPS):
        salt.step(dt)
        if salt.cold_pump.idle() and salt.hot_pump.idle():
            break
    steps.append(
        {
            "component": "salt",
            "action": "stop_pumps",
            "detail": {"pumps_running": salt.pumps_running(), "flow_kg_s": round(salt.flow_kg_s(), 3)},
        }
    )
    if not (salt.cold_pump.idle() and salt.hot_pump.idle()):
        violations.append("pumps_not_idle")

    try:
        receiver.header.close()
    except PlantError as error:  # pragma: no cover - 关阀失败属于硬件故障
        violations.append(error.message)
    steps.append({"component": "receiver", "action": "close_header", "detail": {"state": receiver.header.state()}})

    return {"ok": not violations, "steps": steps, "violations": violations}
