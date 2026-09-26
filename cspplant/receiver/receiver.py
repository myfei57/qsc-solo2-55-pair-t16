"""吸热器状态机。

状态在 ``cold``（冷态）、``circulating``（已通盐）、``at_power``（带负荷）、
``derated``（降额运行）、``tripped``（跳闸闩锁）之间切换。每次投运都必须先
确认流量、再通盐、最后才允许带负荷；越限直接跳闸，复位要以闩锁已清除为前提。
"""

from __future__ import annotations

from ..config import ReceiverLimits
from ..errors import InterlockError
from .derate import DerateController
from .flow import FlowGate, FlowStatus
from .header import SaltHeader
from .sensor import TemperatureSensor
from .snapshot import ReceiverSnapshot
from .thermal import ThermalModel
from .threshold import ThresholdModel
from .valve import InletValve

COLD = "cold"
CIRCULATING = "circulating"
AT_POWER = "at_power"
DERATED = "derated"
TRIPPED = "tripped"


class Receiver:
    """吸热器：流量门控、通盐、受热与保护。"""

    def __init__(
        self,
        limits: ReceiverLimits,
        valve: InletValve,
        gate: FlowGate,
        sensor: TemperatureSensor,
        thermal: ThermalModel,
        derate: DerateController,
        header: SaltHeader,
        threshold: ThresholdModel,
    ) -> None:
        self._limits = limits
        self._valve = valve
        self._gate = gate
        self._sensor = sensor
        self._thermal = thermal
        self._derate = derate
        self._header = header
        self._threshold = threshold
        self._state = COLD
        self._trip_reason = ""
        self._trips = 0
        self._heat_steps = 0
        self._last_flow: FlowStatus | None = None
        self._last_threshold: dict = {"ok": True, "threshold_kg_s": self._threshold.threshold_kg_s()}

    @property
    def limits(self) -> ReceiverLimits:
        return self._limits

    @property
    def valve(self) -> InletValve:
        return self._valve

    @property
    def gate(self) -> FlowGate:
        return self._gate

    @property
    def sensor(self) -> TemperatureSensor:
        return self._sensor

    @property
    def thermal(self) -> ThermalModel:
        return self._thermal

    @property
    def derate(self) -> DerateController:
        return self._derate

    @property
    def header(self) -> SaltHeader:
        return self._header

    @property
    def threshold(self) -> ThresholdModel:
        return self._threshold

    def state(self) -> str:
        """返回当前状态。"""

        return self._state

    def verify_flow(self, flow_kg_s: float, dt: float) -> FlowStatus:
        """推进流量确认门控，返回本次判定结果。"""

        status = self._gate.update(flow_kg_s, dt)
        self._last_flow = status
        if status.confirmed and self._state == COLD:
            self._state = CIRCULATING
        if not status.sufficient and self._state in (AT_POWER, DERATED):
            self._state = CIRCULATING
        return status

    def flow_confirmed(self) -> bool:
        """流量是否已确认。"""

        return self._gate.confirmed()

    def admit(self) -> str:
        """通盐：以流量确认为前提把熔盐放进吸热器。"""

        if self._state == TRIPPED:
            raise InterlockError("吸热器已跳闸，未复位前禁止通盐", component="receiver")
        if not self._gate.confirmed():
            raise InterlockError("流量未确认，禁止通盐", component="receiver")
        state = self._header.admit(True)
        self._valve.set_target(100.0)
        self._state = CIRCULATING
        return state

    def open_valve(self, pct: float) -> float:
        """调整进口阀开度。"""

        return self._valve.set_target(pct)

    def step_valve(self, dt: float) -> float:
        """推进阀门一个步长，返回推进后的开度。"""

        return self._valve.step(dt)

    def ceiling_pct(self, flow_kg_s: float) -> float:
        """给出本地的出力上限：取降额上限与低流量联锁中更保守的一个。"""

        thermal_limit = 100.0 * self._thermal.derate_factor()
        ceiling = min(self._derate.current_ceiling_pct(), thermal_limit)
        verdict = self._threshold.evaluate(flow_kg_s)
        self._last_threshold = verdict
        if not verdict["ok"]:
            return 0.0
        return ceiling

    def heat(self, power_kw: float, flow_kg_s: float, dt: float) -> dict:
        """带负荷运行一个步长，返回受热报告。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        if self._state == TRIPPED:
            raise InterlockError(f"吸热器已跳闸（{self._trip_reason}），禁止继续受热", component="receiver")
        if self._state == COLD or self._header.state() != "admitted":
            raise InterlockError("尚未通盐，禁止带负荷", component="receiver")

        self._heat_steps += 1
        temperature = self._thermal.update(power_kw, flow_kg_s, dt)
        filtered = self._sensor.read(temperature)
        state = self._derate.evaluate(filtered)
        verdict = self._threshold.evaluate(flow_kg_s)
        self._last_threshold = verdict

        violations: list[str] = []
        if not verdict["ok"]:
            violations.append("low_flow")
        if self._limits.overtemperature(filtered):
            self.trip("overtemperature")
            violations.append("overtemperature")
        elif state.active or self._limits.derate_band(filtered):
            self._state = DERATED
        else:
            self._state = AT_POWER

        return {
            "ok": not violations,
            "state": self._state,
            "temperature_c": round(filtered, 2),
            "raw_temperature_c": round(temperature, 2),
            "power_kw": round(float(power_kw), 2),
            "flow_kg_s": round(float(flow_kg_s), 3),
            "derate": state.as_dict(),
            "flow_verdict": verdict,
            "violations": violations,
        }

    def trip(self, reason: str) -> str:
        """跳闸并记录原因。"""

        self._state = TRIPPED
        self._trip_reason = str(reason)
        self._trips += 1
        self._valve.set_target(0.0)
        self._header.close()
        return self._state

    def reset(self, latch_cleared: bool) -> str:
        """复位跳闸；闩锁未清除即拒绝。"""

        if self._state != TRIPPED:
            raise InterlockError("吸热器未跳闸，无需复位", component="receiver")
        if not latch_cleared:
            raise InterlockError("故障闩锁未复位，禁止复位吸热器", component="receiver")
        self._thermal.reset()
        self._sensor.reset(self._limits.ambient_c)
        self._trip_reason = ""
        self._state = COLD
        return self._state

    def trip_reason(self) -> str:
        """返回最近一次跳闸原因。"""

        return self._trip_reason

    def trips(self) -> int:
        """返回累计跳闸次数。"""

        return self._trips

    def heat_steps(self) -> int:
        """返回累计带负荷步数。"""

        return self._heat_steps

    def output_temperature_c(self) -> float:
        """返回滤波后的出口温度。"""

        return self._sensor.value_c()

    def snapshot(self) -> ReceiverSnapshot:
        """返回吸热器状态快照。"""

        flow = self._last_flow
        return ReceiverSnapshot(
            state=self._state,
            temperature_c=self._sensor.value_c(),
            peak_c=self._sensor.peak_c(),
            valve_pct=self._valve.position_pct(),
            flow_kg_s=0.0 if flow is None else flow.flow_kg_s,
            flow_confirmed=self._gate.confirmed(),
            hold_s=self._gate.hold_s(),
            derated=self._derate.active(),
            ceiling_pct=self._derate.ceiling_pct(self._sensor.value_c()),
            trip_reason=self._trip_reason,
        )
