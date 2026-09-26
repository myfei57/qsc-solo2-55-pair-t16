"""熔盐回路控制器。

把罐、泵、水力模型、标定与持久化串成一个回路：循环时推进两台泵并汇总净流量，
同时按流量增减冷热罐存量；切换走 :class:`~cspplant.salt.handoff.TankHandoff`
的固定顺序；标定走 :class:`~cspplant.salt.calibrate.FlowCalibrator`，标定结果
直接改变联锁门限。
"""

from __future__ import annotations

import threading

from ..config import SaltLimits
from ..errors import InterlockError
from ..receiver.header import SaltHeader
from ..receiver.thermal import ThermalModel
from ..receiver.threshold import ThresholdModel
from .calibrate import CalibrationRecord, FlowCalibrator
from .handoff import HandoffReport, TankHandoff
from .hydraulic import HydraulicLoop
from .persist import SaltPersistence
from .pump import SaltPump
from .snapshot import SaltSnapshot
from .tank import SaltTank

COLD_TANK_CAPACITY_KG = 900_000.0
HOT_TANK_CAPACITY_KG = 900_000.0


class SaltLoop:
    """冷热罐、盐泵与母管的回路控制器。"""

    def __init__(
        self,
        limits: SaltLimits,
        threshold: ThresholdModel,
        thermal: ThermalModel,
        header: SaltHeader,
        persistence: SaltPersistence,
    ) -> None:
        self._limits = limits
        self._threshold = threshold
        self._thermal = thermal
        self._header = header
        self._persistence = persistence
        self._cold_tank = SaltTank("cold-tank", COLD_TANK_CAPACITY_KG, COLD_TANK_CAPACITY_KG * 0.82)
        self._hot_tank = SaltTank("hot-tank", HOT_TANK_CAPACITY_KG, HOT_TANK_CAPACITY_KG * 0.12)
        self._cold_pump = SaltPump("cold-pump", limits)
        self._hot_pump = SaltPump("hot-pump", limits)
        self._hydraulic = HydraulicLoop(limits)
        self._handoff = TankHandoff(limits)
        self._calibrator = FlowCalibrator(limits, threshold)
        self._lock = threading.RLock()
        self._restore()

    def _restore(self) -> None:
        """重启后恢复罐位与流量基线。"""

        levels = self._persistence.read_levels()
        if levels is not None:
            self._cold_tank.set_level_pct(levels["cold_pct"])
            self._hot_tank.set_level_pct(levels["hot_pct"])
        baseline = self._persistence.read_baseline()
        if baseline is not None and baseline["baseline_kg_s"] > 0:
            self._threshold.calibrate(baseline["baseline_kg_s"])

    @property
    def limits(self) -> SaltLimits:
        return self._limits

    @property
    def cold_tank(self) -> SaltTank:
        return self._cold_tank

    @property
    def hot_tank(self) -> SaltTank:
        return self._hot_tank

    @property
    def cold_pump(self) -> SaltPump:
        return self._cold_pump

    @property
    def hot_pump(self) -> SaltPump:
        return self._hot_pump

    @property
    def hydraulic(self) -> HydraulicLoop:
        return self._hydraulic

    @property
    def handoff(self) -> TankHandoff:
        return self._handoff

    @property
    def calibrator(self) -> FlowCalibrator:
        return self._calibrator

    @property
    def threshold(self) -> ThresholdModel:
        return self._threshold

    def start_cold(self) -> float:
        """启冷泵。"""

        with self._lock:
            if self._cold_tank.at_min():
                raise InterlockError("冷盐罐已到下限，禁止启冷泵", component="salt")
            return self._cold_pump.start()

    def stop_cold(self) -> float:
        """停冷泵。"""

        with self._lock:
            return self._cold_pump.stop()

    def start_hot(self) -> float:
        """启热泵；冷泵仍在运转即拒绝。"""

        with self._lock:
            if self._cold_pump.running():
                raise InterlockError("冷盐泵仍在运行，禁止启热盐泵", component="salt")
            return self._hot_pump.start()

    def stop_hot(self) -> float:
        """停热泵。"""

        with self._lock:
            return self._hot_pump.stop()

    def pumps_running(self) -> int:
        """返回在运泵数量。"""

        return sum(1 for pump in (self._cold_pump, self._hot_pump) if pump.running())

    def flow_kg_s(self) -> float:
        """返回最近一次汇总的净流量。"""

        return self._hydraulic.net_flow_kg_s()

    def step(self, dt: float) -> dict:
        """推进一个循环步：泵升速、净流量汇总、罐位记账、母管压力更新。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        with self._lock:
            flow = self._hydraulic.update([self._cold_pump, self._hot_pump], dt)
            moved = 0.0
            if flow > 0.0:
                try:
                    moved = self._cold_tank.draw(flow * dt)
                    self._hot_tank.fill(moved)
                except InterlockError:
                    moved = 0.0
            pressure = self._header.update(flow, self.pumps_running(), dt)
            self._persistence.save_levels(self._cold_tank.level_pct(), self._hot_tank.level_pct(), 0.0)
            return {
                "flow_kg_s": round(flow, 3),
                "moved_kg": round(moved, 3),
                "pressure_mpa": round(pressure, 3),
                "pumps_running": self.pumps_running(),
                "overpressure": self._header.overpressure(),
            }

    def changeover(self, dt: float = 1.0) -> HandoffReport:
        """执行冷热罐切换。"""

        with self._lock:
            return self._handoff.changeover(
                self._cold_pump,
                self._hot_pump,
                self._cold_tank,
                self._hot_tank,
                self._header,
                self._hydraulic,
                dt,
            )

    def calibrate_flow(self, measured_kg_s: float, now: float, operator: str = "console") -> CalibrationRecord:
        """按实测流量重新标定基线并落盘。"""

        with self._lock:
            record = self._calibrator.calibrate(measured_kg_s, now, operator)
            self._persistence.save_baseline(record.baseline_kg_s, record.epoch)
            return record

    def threshold_kg_s(self) -> float:
        """返回当前低流量联锁门限。"""

        return self._threshold.threshold_kg_s()

    def disturb(self, heat_fraction: float) -> dict:
        """注入一次热负荷扰动：热罐多进盐、冷罐多出盐。"""

        with self._lock:
            delta = max(0.0, min(1.0, float(heat_fraction))) * 2_000.0
            self._cold_tank.draw(delta)
            added = self._hot_tank.fill(delta)
            return {
                "moved_kg": round(added, 1),
                "cold_level_pct": round(self._cold_tank.level_pct(), 3),
                "hot_level_pct": round(self._hot_tank.level_pct(), 3),
                "temperature_c": round(self._thermal.value_c(), 2),
            }

    def status(self) -> dict:
        """汇总熔盐回路状态。"""

        return {
            "cold_tank": self._cold_tank.as_dict(),
            "hot_tank": self._hot_tank.as_dict(),
            "cold_pump": self._cold_pump.as_dict(),
            "hot_pump": self._hot_pump.as_dict(),
            "net_flow_kg_s": round(self._hydraulic.net_flow_kg_s(), 3),
            "opposed_steps": self._hydraulic.opposed_steps(),
            "hydraulic_updates": self._hydraulic.updates(),
            "header": self._header.status().as_dict(),
            "header_admits": self._header.admits(),
            "header_overpressure_events": self._header.overpressure_events(),
            "baseline_kg_s": round(self._threshold.baseline_kg_s, 3),
            "threshold_kg_s": round(self._threshold.threshold_kg_s(), 3),
            "threshold_evaluations": self._threshold.evaluations(),
            "epoch": self._threshold.epoch,
            "changeovers": self._handoff.changeovers(),
            "calibrations": [record.as_dict() for record in self.calibrator.records()],
        }

    def snapshot(self) -> SaltSnapshot:
        """返回熔盐回路状态快照对象。"""

        return SaltSnapshot(
            cold_level_pct=self._cold_tank.level_pct(),
            hot_level_pct=self._hot_tank.level_pct(),
            cold_flow_kg_s=self._cold_pump.flow_kg_s(),
            hot_flow_kg_s=self._hot_pump.flow_kg_s(),
            net_flow_kg_s=self._hydraulic.net_flow_kg_s(),
            pumps_running=self.pumps_running(),
            opposed_steps=self._hydraulic.opposed_steps(),
            baseline_kg_s=self._threshold.baseline_kg_s,
            threshold_kg_s=self._threshold.threshold_kg_s(),
            epoch=self._threshold.epoch,
        )
