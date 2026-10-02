"""启动运行的断点续做与有序回退。

启动控制链切成五个阶段，顺序写死：迁移（参数核对）→ 追日 → 熔盐循环 →
吸热器受热 → 汽机建负荷。每步完成即落检查点；进程重启后 :meth:`recover`
按阶段判定续做或回退（粒度定死为阶段），迁移阶段一律重做参数核对，核对
不过就按相反顺序把已有现场效果的阶段退干净。续做动作幂等：检查点里已
完成的步骤不重复执行，现场效果已在的步骤直接记为完成。恢复过程、参数
核对与审计共用同一份步骤记录，控制台看到的就是审计落盘的内容。
"""

from __future__ import annotations

from typing import Callable

from ..clock import Clock
from ..config import PlatformConfig
from ..errors import InterlockError, PlantError
from ..eventbus import Event, EventBus
from ..field.field import FieldController
from ..heliostat.sun import SunPosition
from ..receiver.receiver import AT_POWER, DERATED, Receiver
from ..salt.loop import SaltLoop
from ..steam.system import SteamSystem
from ..store.jsonfile import JsonStore
from . import checkpoint as ck
from .checkpoint import CheckpointStore, RunCheckpoint
from .params import diff_snapshots, fingerprint, parameter_snapshot, run_params
from .plan import OUTCOME_ROLLBACK, build_plan

MIGRATION = "migration"
TRACKING = "tracking"
CIRCULATION = "circulation"
RECEIVER = "receiver"
STEAM = "steam"

PHASE_ORDER = (MIGRATION, TRACKING, CIRCULATION, RECEIVER, STEAM)

MAX_STEPS = 1800

# 阶段 → 步骤表：(阶段, 步骤, 组件, 动作)，顺序即执行顺序。
_STEP_DEFS: tuple[tuple[str, str, str, str], ...] = (
    (MIGRATION, "snapshot_params", "recovery", "snapshot_params"),
    (MIGRATION, "verify_params", "recovery", "verify_params"),
    (TRACKING, "acquire", "field", "acquire"),
    (TRACKING, "persist_irradiance", "field", "persist_irradiance"),
    (TRACKING, "track", "field", "track"),
    (CIRCULATION, "start_cold_pump", "salt", "start_cold_pump"),
    (CIRCULATION, "confirm_flow", "receiver", "confirm_flow"),
    (CIRCULATION, "admit_salt", "receiver", "admit_salt"),
    (CIRCULATION, "open_inlet_valve", "receiver", "open_inlet_valve"),
    (RECEIVER, "first_heat", "receiver", "first_heat"),
    (STEAM, "raise_load", "steam", "raise_load"),
)

# 事件载荷里与审计记录字段同名的键不能带进去。
_RESERVED = {"ts", "component", "action", "type", "severity", "asset", "message"}


class _RunContext:
    """一次执行用到的运行参数与核对结果。"""

    __slots__ = ("params", "dt", "sun", "now", "snapshot", "fingerprint", "changed", "checks")

    def __init__(
        self,
        params: dict,
        dt: float,
        sun: SunPosition,
        now: float,
        snapshot: dict,
        changed: list[dict],
        checks: list[dict],
    ) -> None:
        self.params = params
        self.dt = dt
        self.sun = sun
        self.now = now
        self.snapshot = snapshot
        self.fingerprint = fingerprint(snapshot)
        self.changed = changed
        self.checks = checks


class RecoveryService:
    """启动运行的检查点、恢复判定与执行。"""

    def __init__(
        self,
        config: PlatformConfig,
        clock: Clock,
        bus: EventBus,
        store: JsonStore,
        *,
        field: FieldController,
        salt: SaltLoop,
        receiver: Receiver,
        steam: SteamSystem,
        sun_at: Callable[[float | None], SunPosition],
        latched: Callable[[], bool],
        next_run_id: Callable[[], str],
    ) -> None:
        self._config = config
        self._clock = clock
        self._bus = bus
        self._checkpoints = CheckpointStore(store)
        self._field = field
        self._salt = salt
        self._receiver = receiver
        self._steam = steam
        self._sun_at = sun_at
        self._latched = latched
        self._next_run_id = next_run_id
        # 每步落盘后的回调，供故障注入与演示使用（模拟掉电）。
        self.step_observer: Callable[[str, str], None] | None = None

    # ---- 查询 ---------------------------------------------------------

    def interrupted(self) -> bool:
        """是否存在需要恢复的未完成运行。"""

        checkpoint = self._checkpoints.load()
        return checkpoint is not None and checkpoint.state in ck.RECOVERABLE

    def summary(self) -> dict:
        """轻量恢复状态，并入平台状态快照。"""

        checkpoint = self._checkpoints.load()
        if checkpoint is None:
            return {"interrupted": False, "run_id": "", "state": "", "phases": {}}
        return {
            "interrupted": checkpoint.state in ck.RECOVERABLE,
            "run_id": checkpoint.run_id,
            "state": checkpoint.state,
            "phases": {phase.name: phase.state for phase in checkpoint.phases},
        }

    def status(self) -> dict:
        """完整恢复状态：检查点 + 恢复计划预览 + 最近一次报告。"""

        checkpoint = self._checkpoints.load()
        payload: dict = {
            "interrupted": checkpoint is not None and checkpoint.state in ck.RECOVERABLE,
            "checkpoint": checkpoint.as_dict() if checkpoint is not None else None,
            "plan": None,
            "last_report": self._checkpoints.load_report(),
        }
        if payload["interrupted"] and checkpoint is not None:
            payload["plan"] = self._plan_for(checkpoint)
        return payload

    def _plan_for(self, checkpoint: RunCheckpoint, overrides: dict | None = None) -> dict:
        """只读地构造恢复计划（控制台预览与 recover 共用同一套判定）。"""

        params = self._merged_params(checkpoint, overrides)
        current = parameter_snapshot(self._config, params)
        changed = diff_snapshots(checkpoint.snapshot, current)
        checks = self._param_checks(params)
        plan = build_plan(checkpoint, changed, checks, PHASE_ORDER)
        return {**plan.as_dict(), "params": params}

    # ---- 启动与恢复 ----------------------------------------------------

    def start_run(self, dni_w_m2: float, load_mw: float, minute_of_day: float | None = None, dt: float = 1.0) -> dict:
        """开一次新的启动运行，逐步落检查点执行到带负荷。"""

        existing = self._checkpoints.load()
        if existing is not None and existing.state in ck.RECOVERABLE:
            raise InterlockError(
                f"存在未完成的启动运行 {existing.run_id}，先恢复（recover）再开新运行",
                component="recovery",
            )
        run_id = self._next_run_id()
        params = run_params(dni_w_m2, load_mw, minute_of_day)
        snapshot = parameter_snapshot(self._config, params)
        checkpoint = RunCheckpoint.new(run_id, "startup", snapshot, fingerprint(snapshot), self._clock.now(), PHASE_ORDER)
        self._checkpoints.save(checkpoint)
        self._publish(
            "startup_run_started",
            run_id,
            message=f"启动运行 {run_id} 开始",
            fingerprint=checkpoint.fingerprint[:12],
        )
        report = self._new_report(checkpoint)
        ctx = self._context(params, dt)
        self._execute_phases(checkpoint, ctx, report)
        return self._finalize(checkpoint, report)

    def recover(self, overrides: dict | None = None, dt: float = 1.0) -> dict:
        """恢复未完成的启动：先重做迁移核对参数，再按阶段续做或回退。

        ``overrides`` 可带 ``dni_w_m2`` / ``load_mw`` / ``minute_of_day``，
        用于停机期间参数需要调整的场景；缺省沿用检查点里登记的运行参数。
        重复调用是安全的：已完成的运行直接返回 ``no_interrupted_run``。
        """

        checkpoint = self._checkpoints.load()
        if checkpoint is None or checkpoint.state not in ck.RECOVERABLE:
            return {
                "recovered": False,
                "reason": "no_interrupted_run",
                "ok": True,
                "steps": [],
                "violations": [],
            }
        params = self._merged_params(checkpoint, overrides)
        ctx = self._context(params, dt, checkpoint=checkpoint)
        plan = build_plan(checkpoint, ctx.changed, ctx.checks, PHASE_ORDER)
        for decision in plan.decisions:
            self._publish(
                "phase_decision",
                checkpoint.run_id,
                phase=decision.phase,
                decision=decision.decision,
                reason=decision.reason,
                message=f"{decision.phase}: {decision.reason}",
            )
        # 迁移阶段一律重做：清掉上一轮记录，重新核对参数。
        checkpoint.reset_phase(MIGRATION, self._clock.now())
        self._checkpoints.save(checkpoint)

        report = self._new_report(checkpoint)
        report["recovered"] = True
        report["recovery"] = plan.as_dict()
        report["recovery"]["params"] = params
        self._execute_phases(checkpoint, ctx, report)
        if checkpoint.state == ck.RUN_FAILED and checkpoint.phase(MIGRATION).state == ck.FAILED:
            self._rollback(checkpoint, ctx, report)
        return self._finalize(checkpoint, report)

    # ---- 执行 ---------------------------------------------------------

    def _execute_phases(self, checkpoint: RunCheckpoint, ctx: _RunContext, report: dict) -> None:
        """按固定顺序执行各阶段；已完成阶段整段跳过，失败即停。"""

        for phase in PHASE_ORDER:
            record = checkpoint.phase(phase)
            if record.state == ck.DONE:
                self._skip_done_phase(checkpoint, record, report)
                continue
            record.state = ck.RUNNING
            record.updated_ts = self._clock.now()
            self._checkpoints.save(checkpoint)
            if not self._execute_steps(checkpoint, record, ctx, report):
                record.state = ck.FAILED
                record.updated_ts = self._clock.now()
                checkpoint.state = ck.RUN_FAILED
                checkpoint.updated_ts = self._clock.now()
                self._checkpoints.save(checkpoint)
                if phase == MIGRATION:
                    self._publish(
                        "migration_rejected",
                        checkpoint.run_id,
                        severity="warning",
                        changed_keys=[item["key"] for item in ctx.changed],
                        failed_checks=[item["name"] for item in ctx.checks if not item["ok"]],
                        message="参数核对未通过，不能带着旧假设往下走",
                    )
                return
            record.state = ck.DONE
            record.updated_ts = self._clock.now()
            if phase == MIGRATION:
                # 迁移通过即接纳当前参数：检查点换成新指纹，后续阶段按新假设走。
                checkpoint.snapshot = ctx.snapshot
                checkpoint.fingerprint = ctx.fingerprint
                self._publish(
                    "migration_verified",
                    checkpoint.run_id,
                    changed_keys=[item["key"] for item in ctx.changed],
                    consistent=not ctx.changed,
                    message="参数核对通过",
                )
            checkpoint.updated_ts = self._clock.now()
            self._checkpoints.save(checkpoint)

    def _execute_steps(self, checkpoint: RunCheckpoint, record: ck.PhaseRecord, ctx: _RunContext, report: dict) -> bool:
        """逐个执行阶段内步骤；已完成或效果已在的步骤幂等跳过。"""

        for phase, step_name, component, action in _STEP_DEFS:
            if phase != record.name:
                continue
            existing = record.step(step_name)
            if existing is not None and existing.state == ck.DONE:
                self._record_step(report, phase, step_name, component, action, True, existing.detail, "检查点已完成")
                self._publish_step_skipped(checkpoint, phase, step_name, "检查点已完成，幂等跳过")
                continue
            present, effect_detail = self._effect_present(phase, step_name, ctx)
            if present:
                detail = {"effect_present": True, **effect_detail}
                record.mark_step(step_name, ck.DONE, self._clock.now(), detail)
                self._save(checkpoint)
                self._record_step(report, phase, step_name, component, action, True, detail, "现场效果已在")
                self._publish_step_skipped(checkpoint, phase, step_name, "现场效果已在，记为完成不重复执行")
                continue
            try:
                detail, violations = self._execute_step(phase, step_name, ctx)
            except PlantError as error:
                record.mark_step(step_name, ck.FAILED, self._clock.now(), {"error": error.message})
                self._save(checkpoint)
                report["violations"].append(error.message)
                self._record_step(report, phase, step_name, component, action, False, {"error": error.message}, None)
                self._publish_step(component, action, checkpoint.run_id, {"error": error.message}, severity="warning")
                return False
            if violations:
                record.mark_step(step_name, ck.FAILED, self._clock.now(), detail)
                self._save(checkpoint)
                report["violations"].extend(violations)
                self._record_step(report, phase, step_name, component, action, False, detail, None)
                self._publish_step(component, action, checkpoint.run_id, detail, severity="warning")
                return False
            record.mark_step(step_name, ck.DONE, self._clock.now(), detail)
            self._save(checkpoint)
            self._record_step(report, phase, step_name, component, action, False, detail, None)
            self._publish_step(component, action, checkpoint.run_id, detail)
            if self.step_observer is not None:
                self.step_observer(phase, step_name)
        return True

    def _execute_step(self, phase: str, step: str, ctx: _RunContext) -> tuple[dict, list[str]]:
        """执行一个步骤，返回（明细， 违约项）；联锁拒绝抛 :class:`PlantError`。"""

        if step == "snapshot_params":
            return {
                "fingerprint": ctx.fingerprint[:12],
                "changed_keys": [item["key"] for item in ctx.changed],
            }, []
        if step == "verify_params":
            failed = [item["name"] for item in ctx.checks if not item["ok"]]
            detail = {"consistent": not ctx.changed, "checks": ctx.checks}
            return detail, [f"param_check:{name}" for name in failed]
        if step == "acquire":
            sample = self._field.acquire(ctx.params["dni_w_m2"], ctx.now)
            return sample.as_dict(), [] if sample.accepted else ["irradiance_rejected"]
        if step == "persist_irradiance":
            durable = self._field.persist_irradiance()
            detail = {
                "dni_w_m2": round(durable, 2),
                "matches_current": self._field.meter.durable_matches_current(),
            }
            return detail, [] if durable > 0.0 else ["irradiance_not_persisted"]
        if step == "track":
            tracked = self._field.track(ctx.sun, ctx.now, self._latched())
            detail = {
                "mode": tracked.get("mode", ""),
                "aimed": tracked.get("aimed", 0),
                "reason": tracked.get("reason", ""),
            }
            return detail, list(tracked["violations"])
        if step == "start_cold_pump":
            target = self._salt.start_cold()
            return {"target_flow_kg_s": round(target, 3)}, []
        if step == "confirm_flow":
            for _ in range(MAX_STEPS):
                self._salt.step(ctx.dt)
                status = self._receiver.verify_flow(self._salt.flow_kg_s(), ctx.dt)
                if status.confirmed:
                    break
            detail = {
                "flow_kg_s": round(self._salt.flow_kg_s(), 3),
                "hold_s": round(self._receiver.gate.hold_s(), 3),
                "confirmed": self._receiver.flow_confirmed(),
            }
            return detail, [] if self._receiver.flow_confirmed() else ["flow_not_confirmed"]
        if step == "admit_salt":
            state = self._receiver.admit()
            return {"header": state, "valve_target_pct": self._receiver.valve.target_pct()}, []
        if step == "open_inlet_valve":
            for _ in range(MAX_STEPS):
                self._receiver.step_valve(ctx.dt)
                if self._receiver.valve.at_target():
                    break
            detail = {
                "position_pct": round(self._receiver.valve.position_pct(), 2),
                "at_target": self._receiver.valve.at_target(),
            }
            return detail, [] if self._receiver.valve.at_target() else ["valve_not_settled"]
        if step == "first_heat":
            heat = self._receiver.heat(self._field.thermal_power_kw(), self._salt.flow_kg_s(), ctx.dt)
            detail = {
                "state": heat["state"],
                "temperature_c": heat["temperature_c"],
                "power_kw": heat["power_kw"],
            }
            return detail, list(heat["violations"])
        if step == "raise_load":
            load = self._steam.load(ctx.params["load_mw"])
            detail = {"load_mw": round(load.load_mw, 3), "target_mw": round(ctx.params["load_mw"], 3)}
            return detail, list(load.violations)
        raise PlantError(f"未知步骤 {phase}/{step}", component="recovery")

    def _effect_present(self, phase: str, step: str, ctx: _RunContext) -> tuple[bool, dict]:
        """幂等核对：步骤的现场效果是否已经在了。"""

        if step == "start_cold_pump":
            return self._salt.cold_pump.running(), {"flow_kg_s": round(self._salt.cold_pump.flow_kg_s(), 3)}
        if step == "confirm_flow":
            return self._receiver.flow_confirmed(), {"hold_s": round(self._receiver.gate.hold_s(), 3)}
        if step == "admit_salt":
            return self._receiver.header.state() == "admitted", {"header": self._receiver.header.state()}
        if step == "open_inlet_valve":
            present = self._receiver.valve.target_pct() > 0.0 and self._receiver.valve.at_target()
            return present, {"position_pct": round(self._receiver.valve.position_pct(), 2)}
        if step == "first_heat":
            return self._receiver.state() in (AT_POWER, DERATED), {"state": self._receiver.state()}
        if step == "raise_load":
            target = min(float(ctx.params["load_mw"]), self._config.steam.load_max_mw)
            present = self._steam.turbine.main_stop_open() and self._steam.ramp.current_mw() >= target - 1e-6
            return present, {"load_mw": round(self._steam.ramp.current_mw(), 3)}
        return False, {}

    def _rollback(self, checkpoint: RunCheckpoint, ctx: _RunContext, report: dict) -> None:
        """按相反顺序把已有现场效果的阶段退干净。"""

        for phase in reversed(PHASE_ORDER):
            record = checkpoint.phase(phase)
            if phase == MIGRATION or not record.has_effects():
                continue
            detail, violations = self._rollback_phase(phase, ctx)
            record.state = ck.ROLLED_BACK
            record.updated_ts = self._clock.now()
            checkpoint.updated_ts = self._clock.now()
            self._save(checkpoint)
            report["steps"].append(
                {
                    "phase": phase,
                    "step": "rollback",
                    "component": "recovery",
                    "action": f"rollback_{phase}",
                    "skipped": False,
                    "rollback": True,
                    "detail": detail,
                }
            )
            report["violations"].extend(violations)
            self._publish(
                "rollback_phase",
                checkpoint.run_id,
                severity="warning" if violations else "notice",
                phase=phase,
                message=f"阶段 {phase} 已按顺序回退",
                **{key: value for key, value in detail.items() if not isinstance(value, (dict, list))},
            )
        checkpoint.state = ck.RUN_ROLLED_BACK
        checkpoint.updated_ts = self._clock.now()
        self._save(checkpoint)

    def _rollback_phase(self, phase: str, ctx: _RunContext) -> tuple[dict, list[str]]:
        """单个阶段的逆动作，顺序与停运编排一致。"""

        if phase == STEAM:
            shed = self._steam.shed(0.0, self._clock.now())
            return {"load_mw": round(shed.load_mw, 3)}, list(shed.violations)
        if phase == RECEIVER:
            self._receiver.open_valve(0.0)
            state = self._receiver.header.close()
            return {"header": state}, []
        if phase == CIRCULATION:
            if self._salt.cold_pump.running():
                self._salt.stop_cold()
            if self._salt.hot_pump.running():
                self._salt.stop_hot()
            for _ in range(MAX_STEPS):
                self._salt.step(ctx.dt)
                if self._salt.cold_pump.idle() and self._salt.hot_pump.idle():
                    break
            violations = [] if (self._salt.cold_pump.idle() and self._salt.hot_pump.idle()) else ["pumps_not_idle"]
            return {
                "pumps_running": self._salt.pumps_running(),
                "flow_kg_s": round(self._salt.flow_kg_s(), 3),
            }, violations
        if phase == TRACKING:
            moved = self._field.stow_all()
            return {"mirrors": moved}, []
        return {}, []

    # ---- 参数核对 -------------------------------------------------------

    def _param_checks(self, params: dict) -> list[dict]:
        """把当前现场状态逐项对到（可能改过的）参数上。"""

        limits = self._config
        flow = self._salt.flow_kg_s()
        steam = self._steam.status()
        low_flow = limits.salt.low_flow_threshold(limits.salt.flow_baseline_kg_s)
        pressure_ok = steam["load_mw"] <= 0.0 or abs(steam["pressure_mpa"] - limits.steam.pressure_target_mpa) <= limits.steam.pressure_band_mpa
        return [
            {
                "name": "dni_in_range",
                "ok": limits.irradiance.in_range(params["dni_w_m2"]),
                "value": params["dni_w_m2"],
                "limit": [limits.irradiance.dni_min_w_m2, limits.irradiance.dni_max_w_m2],
            },
            {
                "name": "load_within_max",
                "ok": 0.0 <= float(params["load_mw"]) <= limits.steam.load_max_mw,
                "value": params["load_mw"],
                "limit": limits.steam.load_max_mw,
            },
            {
                "name": "temperature_below_max",
                "ok": self._receiver.output_temperature_c() < limits.receiver.temp_max_c,
                "value": round(self._receiver.output_temperature_c(), 2),
                "limit": limits.receiver.temp_max_c,
            },
            {
                "name": "flow_above_threshold",
                "ok": flow <= 0.0 or flow >= low_flow,
                "value": round(flow, 3),
                "limit": round(low_flow, 3),
            },
            {
                "name": "cold_tank_above_min",
                "ok": self._salt.cold_tank.level_pct() >= limits.salt.cold_level_min_pct,
                "value": round(self._salt.cold_tank.level_pct(), 2),
                "limit": limits.salt.cold_level_min_pct,
            },
            {
                "name": "steam_pressure_in_band",
                "ok": pressure_ok,
                "value": steam["pressure_mpa"],
                "limit": [round(limits.steam.pressure_target_mpa - limits.steam.pressure_band_mpa, 3), round(limits.steam.pressure_target_mpa + limits.steam.pressure_band_mpa, 3)],
            },
        ]

    # ---- 内部工具 -------------------------------------------------------

    def _merged_params(self, checkpoint: RunCheckpoint, overrides: dict | None) -> dict:
        """检查点登记的运行参数叠加恢复时的覆盖项。"""

        params = dict(checkpoint.snapshot.get("run", {}))
        for key, value in (overrides or {}).items():
            if value is not None:
                params[key] = value
        return params

    def _context(self, params: dict, dt: float, checkpoint: RunCheckpoint | None = None) -> _RunContext:
        minute = params.get("minute_of_day")
        snapshot = parameter_snapshot(self._config, params)
        changed = [] if checkpoint is None else diff_snapshots(checkpoint.snapshot, snapshot)
        return _RunContext(
            params=params,
            dt=dt,
            sun=self._sun_at(minute),
            now=self._clock.now(),
            snapshot=snapshot,
            changed=changed,
            checks=self._param_checks(params),
        )

    def _new_report(self, checkpoint: RunCheckpoint) -> dict:
        return {
            "ok": False,
            "run_id": checkpoint.run_id,
            "operation": "startup",
            "stage": "running",
            "steps": [],
            "violations": [],
        }

    def _record_step(
        self,
        report: dict,
        phase: str,
        step: str,
        component: str,
        action: str,
        skipped: bool,
        detail: dict,
        skip_reason: str | None,
    ) -> None:
        entry = {
            "phase": phase,
            "step": step,
            "component": component,
            "action": action,
            "skipped": skipped,
            "detail": detail,
        }
        if skip_reason:
            entry["skip_reason"] = skip_reason
        report["steps"].append(entry)

    def _skip_done_phase(self, checkpoint: RunCheckpoint, record: ck.PhaseRecord, report: dict) -> None:
        meta = {step: (component, action) for phase, step, component, action in _STEP_DEFS if phase == record.name}
        for step in record.steps:
            if step.state != ck.DONE:
                continue
            component, action = meta.get(step.name, ("recovery", step.name))
            self._record_step(report, record.name, step.name, component, action, True, step.detail, "检查点已完成")
        self._publish(
            "phase_skipped",
            checkpoint.run_id,
            phase=record.name,
            steps=len(record.steps),
            message=f"阶段 {record.name} 已完成，整段幂等跳过",
        )

    def _finalize(self, checkpoint: RunCheckpoint, report: dict) -> dict:
        if checkpoint.state == ck.RUN_ACTIVE:
            checkpoint.state = ck.RUN_COMPLETED
            checkpoint.updated_ts = self._clock.now()
            self._save(checkpoint)
        ok = checkpoint.state == ck.RUN_COMPLETED
        report["ok"] = ok
        report["stage"] = "done" if ok else self._first_incomplete_phase(checkpoint)
        if ok:
            report["load_mw"] = round(self._steam.ramp.current_mw(), 3)
            report["flow_kg_s"] = round(self._salt.flow_kg_s(), 3)
        action = {
            ck.RUN_COMPLETED: "startup_complete",
            ck.RUN_ROLLED_BACK: "startup_rolled_back",
        }.get(checkpoint.state, "startup_run_failed")
        message = {
            ck.RUN_COMPLETED: f"启动运行 {checkpoint.run_id} 完成",
            ck.RUN_ROLLED_BACK: f"启动运行 {checkpoint.run_id} 已按顺序回退",
        }.get(checkpoint.state, f"启动运行 {checkpoint.run_id} 未完成")
        self._publish(
            action,
            checkpoint.run_id,
            severity="notice" if ok else "warning",
            message=message,
            operation="startup",
            ok=ok,
            violations=list(report["violations"]),
        )
        self._checkpoints.save_report(
            {
                "run_id": checkpoint.run_id,
                "state": checkpoint.state,
                "ok": ok,
                "stage": report["stage"],
                "violations": list(report["violations"]),
                "recovery": report.get("recovery"),
                "ts": round(self._clock.now(), 3),
            }
        )
        return report

    def _first_incomplete_phase(self, checkpoint: RunCheckpoint) -> str:
        for phase in checkpoint.phases:
            if phase.state != ck.DONE:
                return phase.name
        return "done"

    def _save(self, checkpoint: RunCheckpoint) -> None:
        checkpoint.updated_ts = self._clock.now()
        self._checkpoints.save(checkpoint)

    # ---- 审计 ---------------------------------------------------------

    def _publish(self, action: str, run_id: str, severity: str = "notice", message: str = "", **payload: object) -> None:
        self._bus.publish(
            Event.of("recovery", "recovery", action, op_id=run_id, severity=severity, message=message, **payload)
        )

    def _publish_step(self, component: str, action: str, run_id: str, detail: dict, severity: str = "notice") -> None:
        flat = {
            key: value
            for key, value in detail.items()
            if not isinstance(value, (dict, list)) and key not in _RESERVED
        }
        self._bus.publish(Event.of(component, component, action, op_id=run_id, severity=severity, **flat))

    def _publish_step_skipped(self, checkpoint: RunCheckpoint, phase: str, step: str, reason: str) -> None:
        self._publish(
            "step_skipped",
            checkpoint.run_id,
            phase=phase,
            step=step,
            reason=reason,
            message=f"{phase}/{step}: {reason}",
        )
