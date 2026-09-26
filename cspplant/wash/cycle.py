"""清洗循环编排。

一个循环是四步：到镜 → 喷淋 → 开排水阀回收回水 → 托盘排空后停回水泵。第三步
与第四步的顺序是硬性的：托盘没空就停泵会把回水溢到地面。编排把每一步推进到
条件成立才结束，并把溢流风险写进报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..errors import InterlockError
from .drain import DrainSystem
from .truck import WashTruck

MAX_STEPS = 3600

# 喷淋回水中可回收的比例。
RECOVERY_RATIO = 0.85


@dataclass(frozen=True)
class WashReport:
    """一次清洗循环的结果。"""

    ok: bool
    mirror: str
    steps: list[dict] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    used_m3: float = 0.0
    recovered_m3: float = 0.0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "mirror": self.mirror,
            "steps": self.steps,
            "violations": self.violations,
            "used_m3": round(self.used_m3, 4),
            "recovered_m3": round(self.recovered_m3, 4),
        }


class WashCycle:
    """按固定顺序跑完一次清洗。"""

    def __init__(self) -> None:
        self._cycles = 0
        self._overflow_risks = 0

    def run(self, truck: WashTruck, drain: DrainSystem, mirror: str, dt: float = 1.0) -> WashReport:
        """执行一次清洗循环。"""

        steps: list[dict] = []
        violations: list[str] = []
        drain.reset()
        used_before = truck.used_m3()
        returned_before = truck.returned_m3()

        try:
            truck.arrive(mirror)
            truck.start_wash()
        except InterlockError as error:
            violations.append(error.message)
            return WashReport(False, mirror, steps, violations, 0.0, 0.0)
        steps.append({"component": "wash", "action": "arrive", "detail": {"mirror": mirror}})

        per_step = truck.limits.return_flow_m3_h * dt / 3600.0
        for _ in range(MAX_STEPS):
            if truck.complete():
                break
            truck.step(dt)
            drain.receive(per_step * RECOVERY_RATIO)
            truck.record_returned(per_step * RECOVERY_RATIO)
        steps.append(
            {
                "component": "wash",
                "action": "spray",
                "detail": {
                    "progress": round(truck.progress(), 3),
                    "water_m3": round(truck.used_m3() - used_before, 4),
                },
            }
        )
        if not truck.complete():
            violations.append("wash_incomplete")
            return WashReport(False, mirror, steps, violations, truck.used_m3() - used_before, 0.0)

        if not drain.drain_open():
            drain.open_drain()
        steps.append(
            {
                "component": "wash",
                "action": "open_drain",
                "detail": {"tray_m3": round(drain.tray_m3(), 4), "pump_running": drain.pump_running()},
            }
        )

        for _ in range(MAX_STEPS):
            if drain.drained():
                break
            drain.step(dt)
        steps.append(
            {
                "component": "wash",
                "action": "drain",
                "detail": {
                    "tray_m3": round(drain.tray_m3(), 4),
                    "recovered_m3": round(drain.recovered_m3(), 4),
                },
            }
        )

        if not drain.drained():
            violations.append("tray_not_drained")
        try:
            drain.stop_pump()
            steps.append(
                {
                    "component": "wash",
                    "action": "stop_return_pump",
                    "detail": {"tray_m3": round(drain.tray_m3(), 4), "drained": drain.drained()},
                }
            )
        except InterlockError as error:
            self._overflow_risks += 1
            violations.append(error.message)

        # 回收的水回到车载水箱，减少下一次清洗的补水量。
        truck.refill(drain.recovered_m3())
        if not violations:
            self._cycles += 1
        return WashReport(
            ok=not violations,
            mirror=mirror,
            steps=steps,
            violations=violations,
            used_m3=truck.used_m3() - used_before,
            recovered_m3=truck.returned_m3() - returned_before,
        )

    def cycles(self) -> int:
        """返回累计成功循环次数。"""

        return self._cycles

    def overflow_risks(self) -> int:
        """返回累计被拦下的溢流风险次数。"""

        return self._overflow_risks
