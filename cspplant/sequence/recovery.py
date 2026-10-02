"""启动断点、参数核对与按阶段恢复。

恢复判定口径固定为**按阶段**，不按单条步骤猜测：步骤记录只用于阶段内幂等，
不能把未完成阶段当成已完成。每个阶段的恢复策略显式登记：追日阶段允许续做，
其余阶段重启后按顺序回退到稳定边界。参数核对属于启动迁移的一部分，停机期间
配置发生变化时，先按受影响阶段回退，再重新执行后续阶段。
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable

from ..config import config_to_mapping
from ..field.field import FieldController
from ..heliostat.sun import SunPosition
from ..receiver.receiver import Receiver
from ..salt.loop import SaltLoop
from ..steam.system import SteamSystem
from ..store.jsonfile import JsonStore
from .circulation import circulate

STARTUP_DOC = "startup/checkpoint"
STARTUP_RESULT_DOC = "startup/last_result"

TRACKING = "tracking"
CIRCULATION = "circulation"
RECEIVER = "receiver"
STEAM = "steam"
STAGES: tuple[str, ...] = (TRACKING, CIRCULATION, RECEIVER, STEAM)

RESUME = "resume"
ROLLBACK = "rollback"
STAGE_POLICY = {
    TRACKING: RESUME,
    CIRCULATION: ROLLBACK,
    RECEIVER: ROLLBACK,
    STEAM: ROLLBACK,
}

# 每个阶段使用的配置段落；先按段落比对，再回退到受影响最早的阶段。
STAGE_CONFIG_SECTIONS = {
    TRACKING: ("heliostat", "irradiance", "field"),
    CIRCULATION: ("receiver", "salt"),
    RECEIVER: ("receiver", "salt"),
    STEAM: ("steam",),
}


@dataclass
class StartupCheckpoint:
    """一次启动的阶段断点与恢复所需输入。"""

    status: str = "new"
    interrupted_stage: str | None = None
    completed_stages: list[str] = field(default_factory=list)
    stages: dict[str, dict] = field(default_factory=dict)
    config_fingerprints: dict[str, str] = field(default_factory=dict)
    full_config_fingerprint: str = ""
    request: dict = field(default_factory=dict)
    steps_completed: dict[str, list[str]] = field(default_factory=dict)
    recovery_count: int = 0
    updated_at: float = 0.0
    version: int = 1

    @classmethod
    def from_dict(cls, raw: dict) -> "StartupCheckpoint":
        status = str(raw.get("status", "new"))
        interrupted = raw.get("interrupted_stage")
        return cls(
            status=status,
            interrupted_stage=None if interrupted is None else str(interrupted),
            completed_stages=[str(item) for item in raw.get("completed_stages", [])],
            stages={str(key): dict(value) for key, value in dict(raw.get("stages", {})).items()},
            config_fingerprints={str(k): str(v) for k, v in dict(raw.get("config_fingerprints", {})).items()},
            full_config_fingerprint=str(raw.get("full_config_fingerprint", "")),
            request=dict(raw.get("request", {})),
            steps_completed={str(k): [str(x) for x in v] for k, v in dict(raw.get("steps_completed", {})).items()},
            recovery_count=int(raw.get("recovery_count", 0)),
            updated_at=float(raw.get("updated_at", 0.0)),
            version=int(raw.get("version", 1)),
        )

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "interrupted_stage": self.interrupted_stage,
            "completed_stages": list(self.completed_stages),
            "stages": copy.deepcopy(self.stages),
            "config_fingerprints": dict(self.config_fingerprints),
            "full_config_fingerprint": self.full_config_fingerprint,
            "request": dict(self.request),
            "steps_completed": {key: list(values) for key, values in self.steps_completed.items()},
            "recovery_count": self.recovery_count,
            "updated_at": round(self.updated_at, 3),
            "version": self.version,
        }


class StartupRecovery:
    """启动断点存储、恢复计划与阶段回退。"""

    def __init__(self, store: JsonStore, config, now_provider: Callable[[], float]) -> None:
        self._store = store
        self._config = config
        self._now = now_provider

    @property
    def doc_id(self) -> str:
        return STARTUP_DOC

    def now(self) -> float:
        return self._now()

    def load(self) -> StartupCheckpoint | None:
        raw = self._store.read(STARTUP_DOC)
        if not isinstance(raw, dict):
            return None
        return StartupCheckpoint.from_dict(raw)

    def save(self, checkpoint: StartupCheckpoint) -> None:
        checkpoint.updated_at = self._now()
        self._store.write(STARTUP_DOC, checkpoint.as_dict())

    def save_result(self, payload: dict) -> None:
        self._store.write(STARTUP_RESULT_DOC, payload)

    def load_result(self) -> dict | None:
        raw = self._store.read(STARTUP_RESULT_DOC)
        return dict(raw) if isinstance(raw, dict) else None

    def clear_checkpoint(self) -> bool:
        return self._store.remove(STARTUP_DOC)

    def clear(self) -> bool:
        removed = self.clear_checkpoint()
        self._store.remove(STARTUP_RESULT_DOC)
        return removed

    def fingerprints(self, config=None) -> tuple[dict[str, str], str]:
        mapping = config_to_mapping(config or self._config)
        limits = mapping["limits"]
        fingerprints = {
            section: _fingerprint(limits.get(section, {}))
            for sections in STAGE_CONFIG_SECTIONS.values()
            for section in sections
        }
        complete = {section: limits.get(section, {}) for section in sorted(fingerprints)}
        return fingerprints, _fingerprint(complete)

    def begin(
        self,
        *,
        dni_w_m2: float,
        load_mw: float,
        minute_of_day: float | None,
    ) -> StartupCheckpoint:
        section_hashes, full_hash = self.fingerprints()
        checkpoint = StartupCheckpoint(
            status="interrupted",
            request={
                "dni_w_m2": float(dni_w_m2),
                "load_mw": float(load_mw),
                "minute_of_day": None if minute_of_day is None else float(minute_of_day),
            },
            config_fingerprints=section_hashes,
            full_config_fingerprint=full_hash,
        )
        self.save(checkpoint)
        return checkpoint

    def mark_stage_running(self, checkpoint: StartupCheckpoint, stage: str, state: dict | None = None) -> None:
        checkpoint.status = "interrupted"
        checkpoint.interrupted_stage = stage
        checkpoint.stages.setdefault(stage, {})
        if state:
            checkpoint.stages[stage].update(state)
        self.save(checkpoint)

    def mark_step_done(self, checkpoint: StartupCheckpoint, stage: str, step: str, state: dict | None = None) -> None:
        done = checkpoint.steps_completed.setdefault(stage, [])
        if step not in done:
            done.append(step)
        checkpoint.stages.setdefault(stage, {})
        if state:
            checkpoint.stages[stage].update(state)
        checkpoint.status = "interrupted"
        checkpoint.interrupted_stage = stage
        self.save(checkpoint)

    def mark_stage_complete(self, checkpoint: StartupCheckpoint, stage: str, state: dict | None = None) -> None:
        checkpoint.stages.setdefault(stage, {})
        if state:
            checkpoint.stages[stage].update(state)
        checkpoint.stages[stage]["completed"] = True
        if stage not in checkpoint.completed_stages:
            checkpoint.completed_stages.append(stage)
        checkpoint.completed_stages = [item for item in STAGES if item in checkpoint.completed_stages]
        index = STAGES.index(stage)
        checkpoint.interrupted_stage = None if index == len(STAGES) - 1 else STAGES[index + 1]
        checkpoint.status = "complete" if stage == STEAM else "interrupted"
        self.save(checkpoint)

    def parameter_check(self, checkpoint: StartupCheckpoint) -> dict:
        """核对当前配置与断点快照，返回必须先回退到的阶段。"""

        current, full_hash = self.fingerprints()
        changed = sorted(
            section
            for section, value in current.items()
            if checkpoint.config_fingerprints.get(section) != value
        )
        full_changed = checkpoint.full_config_fingerprint not in ("", full_hash)
        earliest_stage = None
        for stage in STAGES:
            if any(section in changed for section in STAGE_CONFIG_SECTIONS[stage]):
                earliest_stage = stage
                break
        # 细分段落之外的平台参数变化也不能沿用旧假设，保守回到第一阶段。
        if full_changed and earliest_stage is None and changed:
            earliest_stage = TRACKING
        return {
            "ok": not changed,
            "changed_sections": changed,
            "full_config_changed": full_changed,
            "earliest_stage": earliest_stage,
            "current_fingerprints": current,
        }

    def acknowledge_current_parameters(self, checkpoint: StartupCheckpoint) -> dict:
        """参数核对重做后，把当前配置登记为本次启动的新核对基线。"""

        fingerprints, full_hash = self.fingerprints()
        report = self.parameter_check(checkpoint)
        checkpoint.config_fingerprints = fingerprints
        checkpoint.full_config_fingerprint = full_hash
        self.save(checkpoint)
        return report

    def plan(self) -> dict:
        checkpoint = self.load()
        if checkpoint is None:
            return {
                "decision_unit": "stage",
                "action": "start",
                "resume_stage": TRACKING,
                "rollback_to_stage": None,
                "parameter_check": {"ok": True, "changed_sections": [], "full_config_changed": False},
                "stages": _stage_views(None),
                "checkpoint": None,
            }

        parameter_check = self.parameter_check(checkpoint)
        if checkpoint.status == "complete":
            action = "done"
            resume_stage = None
            rollback_to = None
        else:
            interrupted = checkpoint.interrupted_stage or TRACKING
            affected = parameter_check["earliest_stage"]
            if affected is not None:
                parameter_rollback_to = affected
                rollback_to = affected
            else:
                parameter_rollback_to = None
                rollback_to = None
            if STAGE_POLICY[interrupted] == ROLLBACK:
                action = "rollback"
                rollback_to = interrupted if rollback_to is None else _earlier_stage(interrupted, rollback_to)
                resume_stage = rollback_to
            elif affected is not None:
                action = "rollback"
                resume_stage = rollback_to
            else:
                action = "resume"
                resume_stage = interrupted
        return {
            "decision_unit": "stage",
            "action": action,
            "resume_stage": resume_stage,
            "rollback_to_stage": rollback_to,
            "interrupted_stage": checkpoint.interrupted_stage,
            "parameter_check": {key: value for key, value in parameter_check.items() if key != "current_fingerprints"},
            "stages": _stage_views(checkpoint),
            "checkpoint": checkpoint.as_dict(),
        }


class StartupOrchestrator:
    """按断点执行启动续做或回退，并保证阶段内已完成步骤不重复。"""

    def __init__(
        self,
        recovery: StartupRecovery,
        field: FieldController,
        salt: SaltLoop,
        receiver: Receiver,
        steam: SteamSystem,
        sun_provider: Callable[[float | None], SunPosition],
        dt: float = 1.0,
    ) -> None:
        self._recovery = recovery
        self._field = field
        self._salt = salt
        self._receiver = receiver
        self._steam = steam
        self._sun_provider = sun_provider
        self._dt = dt

    def start_or_resume(self, dni_w_m2: float, load_mw: float, minute_of_day: float | None) -> dict:
        existing = self._recovery.load()
        recovery_steps: list[dict] = []
        if existing is None:
            previous = self._recovery.load_result()
            if previous is not None and self._runtime_matches_previous(previous, dni_w_m2, load_mw):
                return self._already_complete_report(previous)
            checkpoint = self._recovery.begin(
                dni_w_m2=dni_w_m2, load_mw=load_mw, minute_of_day=minute_of_day
            )
            start_stage = TRACKING
            rollback_to = None
        else:
            checkpoint = existing
            plan = self._recovery.plan()
            parameter_report = plan["parameter_check"]
            parameter_step = {
                "component": "startup",
                "action": "parameter_check",
                "detail": {
                    "ok": parameter_report["ok"],
                    "changed_sections": parameter_report["changed_sections"],
                    "full_config_changed": parameter_report["full_config_changed"],
                },
            }
            recovery_steps.append(parameter_step)
            if plan["action"] == "done":
                return self._done_report(checkpoint, recovery_steps)
            start_stage = plan["resume_stage"]
            policy_rollback_to = plan["interrupted_stage"] if plan["action"] == "rollback" else None
            parameter_rollback_to = plan["rollback_to_stage"]
            checkpoint.recovery_count += 1
            checkpoint.request = {
                "dni_w_m2": float(dni_w_m2),
                "load_mw": float(load_mw),
                "minute_of_day": None if minute_of_day is None else float(minute_of_day),
            }
            self._recovery.save(checkpoint)
            rollback_targets = [stage for stage in (parameter_rollback_to, policy_rollback_to) if stage is not None]
            if rollback_targets:
                target = min(rollback_targets, key=STAGES.index)
                recovery_steps.extend(self._rollback_to(checkpoint, target))
            if parameter_rollback_to is not None:
                changed_before = list(parameter_report["changed_sections"])
                self._recovery.acknowledge_current_parameters(checkpoint)
                recheck = {
                    "component": "startup",
                    "action": "parameter_recheck",
                    "detail": {"ok": True, "changed_sections": changed_before, "baseline_refreshed": True},
                }
                recovery_steps.append(recheck)

        steps = list(recovery_steps)
        violations: list[str] = []
        first_stage_index = STAGES.index(start_stage)
        for stage in STAGES[first_stage_index:]:
            if stage in checkpoint.completed_stages:
                self._restore_completed_stage(checkpoint, stage, recovery_events=steps)
                continue
            report = self._run_stage(checkpoint, stage, steps)
            violations.extend(report.get("violations", []))
            if not report.get("ok", True):
                return {
                    "ok": False,
                    "stage": stage,
                    "steps": steps,
                    "violations": violations,
                    "recovery": self._recovery.plan(),
                }

        load = self._steam.status()["load_mw"]
        result = {
            "ok": True,
            "stage": "done",
            "steps": steps,
            "violations": violations,
            "load_mw": round(load, 3),
            "flow_kg_s": round(self._salt.flow_kg_s(), 3),
            "request": {"dni_w_m2": float(dni_w_m2), "load_mw": float(load_mw)},
        }
        self._recovery.save_result(result)
        self._recovery.clear_checkpoint()
        return {**result, "resumed": existing is not None, "recovery": {"decision_unit": "stage", "action": "complete"}}

    def rollback_and_clear(self, now: float) -> dict:
        checkpoint = self._recovery.load()
        steps: list[dict] = []
        violations: list[str] = []
        handled = False
        if checkpoint is not None:
            handled = True
            completed = list(checkpoint.completed_stages)
            interrupted = checkpoint.interrupted_stage
            if checkpoint.status != "complete" and interrupted is not None and interrupted not in completed:
                completed.append(interrupted)
            for stage in reversed(STAGES):
                if stage not in completed:
                    continue
                self._restore_dependencies_for_rollback(checkpoint, stage)
                report = self._rollback_stage(checkpoint, stage)
                steps.extend(report["steps"])
                violations.extend(report["violations"])
                if stage in checkpoint.completed_stages:
                    checkpoint.completed_stages.remove(stage)
                checkpoint.stages.pop(stage, None)
                self._recovery.save(checkpoint)
            if checkpoint.interrupted_stage is not None:
                checkpoint.interrupted_stage = None
            checkpoint.status = "rolled_back"
            self._recovery.save(checkpoint)
        elif self._recovery.load_result() is not None:
            handled = True
            runtime_checkpoint = self._runtime_checkpoint(now)
            for stage in reversed(STAGES):
                self._restore_dependencies_for_rollback(runtime_checkpoint, stage)
                report = self._rollback_stage(runtime_checkpoint, stage)
                steps.extend(report["steps"])
                violations.extend(report["violations"])
        self._recovery.clear()
        return {"ok": not violations, "steps": steps, "violations": violations, "handled": handled}

    def _runtime_checkpoint(self, now: float) -> StartupCheckpoint:
        return StartupCheckpoint(
            status="complete",
            completed_stages=list(STAGES),
            stages={stage: {"completed": True} for stage in STAGES},
            request={
                "dni_w_m2": self._field.durable_irradiance(),
                "load_mw": self._steam.status()["load_mw"],
                "minute_of_day": None,
            },
            updated_at=now,
        )

    def _runtime_matches_previous(self, previous: dict, dni_w_m2: float, load_mw: float) -> bool:
        request = dict(previous.get("request", {}))
        receiver_ready = self._receiver.state() in ("at_power", "derated") and self._receiver.header.state() == "admitted"
        return (
            bool(previous.get("ok"))
            and receiver_ready
            and self._steam.status()["main_stop_open"]
            and abs(float(request.get("dni_w_m2", -1.0)) - float(dni_w_m2)) < 1e-9
            and abs(float(request.get("load_mw", -1.0)) - float(load_mw)) < 1e-9
        )

    def _already_complete_report(self, previous: dict) -> dict:
        step = {"component": "startup", "action": "already_complete", "detail": {"decision_unit": "stage"}}
        return {
            "ok": True,
            "stage": "done",
            "steps": [step],
            "violations": [],
            "load_mw": previous.get("load_mw", self._steam.status()["load_mw"]),
            "flow_kg_s": self._salt.flow_kg_s(),
            "resumed": True,
            "audit_summary": False,
            "recovery": {"decision_unit": "stage", "action": "done"},
        }

    def _done_report(self, checkpoint: StartupCheckpoint, recovery_steps: list[dict]) -> dict:
        for stage in STAGES:
            if stage in checkpoint.completed_stages:
                self._restore_completed_stage(checkpoint, stage, recovery_events=recovery_steps)
        recovery_steps.append({"component": "startup", "action": "already_complete", "detail": {"decision_unit": "stage"}})
        return {
            "ok": True,
            "stage": "done",
            "steps": recovery_steps,
            "violations": [],
            "load_mw": round(float(checkpoint.request.get("load_mw", 0.0)), 3),
            "flow_kg_s": round(self._salt.flow_kg_s(), 3),
            "resumed": True,
            "audit_summary": False,
            "recovery": {"decision_unit": "stage", "action": "done"},
        }

    def _run_stage(self, checkpoint: StartupCheckpoint, stage: str, steps: list[dict]) -> dict:
        self._recovery.mark_stage_running(checkpoint, stage)
        if stage == TRACKING:
            report = self._run_tracking(checkpoint, steps)
        elif stage == CIRCULATION:
            report = circulate(self._salt, self._receiver, self._dt)
            steps.extend(report["steps"])
        elif stage == RECEIVER:
            report = self._run_receiver(checkpoint, steps)
        else:
            report = self._run_steam(checkpoint, steps)
        if report.get("ok"):
            self._mark_stage_result(checkpoint, stage)
        return report

    def _run_tracking(self, checkpoint: StartupCheckpoint, steps: list[dict]) -> dict:
        request = checkpoint.request
        sun = self._sun_provider(request.get("minute_of_day"))
        done = set(checkpoint.steps_completed.get(TRACKING, []))
        violations: list[str] = []
        aimed = 0

        if "acquire" not in done:
            sample = self._field.acquire(float(request["dni_w_m2"]), self._recovery.now())
            steps.append({"component": "field", "action": "acquire", "detail": sample.as_dict()})
            if not sample.accepted:
                violations.append("irradiance_rejected")
                return {"ok": False, "steps": steps, "violations": violations, "aimed": 0}
            self._recovery.mark_step_done(checkpoint, TRACKING, "acquire", {"sample": sample.as_dict()})
        else:
            sample_state = checkpoint.stages.get(TRACKING, {}).get("sample", {})
            self._field.restore_current_irradiance(
                float(sample_state.get("dni_w_m2", request["dni_w_m2"])),
                float(sample_state.get("ts", self._recovery.now())),
            )
            step = {"component": "startup", "action": "skip_completed_step", "detail": {"stage": TRACKING, "step": "acquire"}}
            steps.append(step)

        if "persist_irradiance" not in done:
            durable = self._field.persist_irradiance()
            steps.append(
                {
                    "component": "field",
                    "action": "persist_irradiance",
                    "detail": {"dni_w_m2": round(durable, 2), "matches_current": self._field.meter.durable_matches_current()},
                }
            )
            if durable <= 0.0:
                violations.append("irradiance_not_persisted")
                return {"ok": False, "steps": steps, "violations": violations, "aimed": 0}
            self._recovery.mark_step_done(checkpoint, TRACKING, "persist_irradiance", {"dni_w_m2": durable})
        else:
            steps.append({"component": "startup", "action": "skip_completed_step", "detail": {"stage": TRACKING, "step": "persist_irradiance"}})

        if "decide" not in done:
            decision = self._field.decide(sun, self._recovery.now())
            steps.append({"component": "field", "action": "decide", "detail": decision.as_dict()})
            self._recovery.mark_step_done(
                checkpoint,
                TRACKING,
                "decide",
                {"mode": decision.mode, "reason": decision.reason, "focuses": decision.focuses},
            )
            done.add("decide")
        else:
            step = {"component": "startup", "action": "skip_completed_step", "detail": {"stage": TRACKING, "step": "decide"}}
            steps.append(step)

        mode = str(checkpoint.stages.get(TRACKING, {}).get("mode", ""))
        focuses = bool(checkpoint.stages.get(TRACKING, {}).get("focuses", mode in ("", "focus")))
        if not focuses:
            return {"ok": True, "steps": steps, "violations": violations, "aimed": 0, "mode": mode}

        if "aim" not in done:
            result = self._field.aim_targets(sun)
            steps.append({"component": "field", "action": "aim", "detail": result})
            if not result.get("ok", False):
                violations.extend(result.get("violations", ["aim_failed"]))
                return {"ok": False, "steps": steps, "violations": violations, "aimed": 0}
            self._recovery.mark_step_done(checkpoint, TRACKING, "aim", {"aimed": result["aimed"]})
            done.add("aim")
        else:
            step = {"component": "startup", "action": "skip_completed_step", "detail": {"stage": TRACKING, "step": "aim"}}
            steps.append(step)

        if "settle" not in done:
            expected = int(checkpoint.stages.get(TRACKING, {}).get("aimed", 0)) or None
            result = self._field.settle_tracking(expected)
            steps.append({"component": "field", "action": "settle", "detail": result})
            expected = expected or result["aimed"]
            if expected and result["aligned"] < expected:
                violations.append("aim_incomplete")
                return {"ok": False, "steps": steps, "violations": violations, "aimed": result["aligned"]}
            self._recovery.mark_step_done(checkpoint, TRACKING, "settle", {"aimed": result["aligned"]})
        else:
            step = {"component": "startup", "action": "skip_completed_step", "detail": {"stage": TRACKING, "step": "settle"}}
            steps.append(step)
        aimed = int(checkpoint.stages.get(TRACKING, {}).get("aimed", self._field.focusing_count()))
        return {"ok": not violations, "steps": steps, "violations": violations, "aimed": aimed, "mode": mode or "focus"}

    def _run_receiver(self, checkpoint: StartupCheckpoint, steps: list[dict]) -> dict:
        heat = self._receiver.heat(self._field.thermal_power_kw(), self._salt.flow_kg_s(), self._dt)
        steps.append({"component": "receiver", "action": "first_heat", "detail": heat})
        return {"ok": heat["ok"], "steps": steps, "violations": heat["violations"], "heat": heat}

    def _run_steam(self, checkpoint: StartupCheckpoint, steps: list[dict]) -> dict:
        load = self._steam.load(float(checkpoint.request["load_mw"]))
        steps.extend(load.steps)
        return {"ok": load.ok, "steps": steps, "violations": load.violations, "load_mw": load.load_mw}

    def _mark_stage_result(self, checkpoint: StartupCheckpoint, stage: str) -> None:
        state: dict
        if stage == TRACKING:
            state = {"aimed": self._field.focusing_count(), "dni_w_m2": self._field.durable_irradiance()}
        elif stage == CIRCULATION:
            state = {"flow_kg_s": self._salt.flow_kg_s(), "header": self._receiver.header.state()}
        elif stage == RECEIVER:
            state = {
                "flow_kg_s": self._salt.flow_kg_s(),
                "temperature_c": self._receiver.output_temperature_c(),
                "state": self._receiver.state(),
            }
        else:
            state = {"load_mw": self._steam.status()["load_mw"]}
        self._recovery.mark_stage_complete(checkpoint, stage, state)

    def _restore_completed_stage(
        self,
        checkpoint: StartupCheckpoint,
        stage: str,
        recovery_events: list[dict],
    ) -> None:
        state = checkpoint.stages.get(stage, {})
        if stage == TRACKING:
            sun = self._sun_provider(checkpoint.request.get("minute_of_day"))
            restored = self._field.restore_tracking(sun, self._recovery.now())
            recovery_events.append({"component": "startup", "action": "restore_stage", "detail": {"stage": stage, **restored}})
        elif stage == CIRCULATION:
            flow = float(state.get("flow_kg_s", self._salt.limits.flow_baseline_kg_s))
            self._salt.restore_circulation(flow)
            self._receiver.restore_circulation(flow)
            recovery_events.append({"component": "startup", "action": "restore_stage", "detail": {"stage": stage, "flow_kg_s": flow}})
        elif stage == RECEIVER:
            self._receiver.restore_power(
                float(state.get("flow_kg_s", self._salt.flow_kg_s())),
                float(state.get("temperature_c", self._receiver.limits.temp_target_c)),
            )
            recovery_events.append({"component": "startup", "action": "restore_stage", "detail": {"stage": stage, "state": self._receiver.state()}})
        elif stage == STEAM:
            load = float(state.get("load_mw", checkpoint.request.get("load_mw", 0.0)))
            self._steam.restore_load(load)
            recovery_events.append({"component": "startup", "action": "restore_stage", "detail": {"stage": stage, "load_mw": load}})

    def _rollback_to(self, checkpoint: StartupCheckpoint, target_stage: str) -> list[dict]:
        steps: list[dict] = []
        affected = set(STAGES[STAGES.index(target_stage) :])
        completed_affected = [stage for stage in reversed(STAGES) if stage in affected and stage in checkpoint.completed_stages]
        interrupted = checkpoint.interrupted_stage
        if interrupted in affected and interrupted not in completed_affected:
            completed_affected.append(interrupted)
            completed_affected.sort(key=STAGES.index, reverse=True)
        for stage in completed_affected:
            self._restore_dependencies_for_rollback(checkpoint, stage)
            report = self._rollback_stage(checkpoint, stage)
            steps.extend(report["steps"])
            if stage in checkpoint.completed_stages:
                checkpoint.completed_stages.remove(stage)
            checkpoint.stages.pop(stage, None)
            checkpoint.steps_completed.pop(stage, None)
            if checkpoint.interrupted_stage == stage:
                checkpoint.interrupted_stage = None
            self._recovery.save(checkpoint)
        decision = {"component": "startup", "action": "rollback_decision", "detail": {"decision_unit": "stage", "target_stage": target_stage}}
        steps.insert(0, decision)
        return steps

    def _restore_dependencies_for_rollback(self, checkpoint: StartupCheckpoint, stage: str) -> None:
        # 回退某阶段前，先按盘上快照重建它所依赖的已完成阶段；回退命令必须按当前世界状态幂等。
        for required in STAGES[: STAGES.index(stage)]:
            if required in checkpoint.completed_stages:
                self._restore_completed_stage(checkpoint, required, [])

    def _rollback_stage(self, checkpoint: StartupCheckpoint, stage: str) -> dict:
        now = self._recovery.now()
        if stage == STEAM:
            if stage not in checkpoint.completed_stages:
                self._steam.restore_load(float(checkpoint.request.get("load_mw", 0.0)))
            report = self._steam.rollback_load(now, self._dt)
            return {"ok": report["ok"], "steps": report["steps"], "violations": report["violations"]}
        if stage == RECEIVER:
            if self._receiver.state() in ("at_power", "derated"):
                state = self._receiver.return_to_circulating()
                action = "recovery_return_to_circulating"
            else:
                if (
                    self._salt.flow_kg_s() >= self._receiver.limits.flow_min_kg_s
                    and self._receiver.header.state() == "admitted"
                ):
                    self._receiver.restore_circulation(self._salt.flow_kg_s())
                state = self._receiver.state()
                action = "recovery_receiver_already_rolled_back"
            return {
                "ok": True,
                "steps": [{"component": "receiver", "action": action, "detail": {"state": state}}],
                "violations": [],
            }
        if stage == CIRCULATION:
            stop = self._salt.stop_cold_idempotent(self._dt)
            receiver = self._receiver.return_to_cold(self._dt)
            return {
                "ok": self._salt.cold_pump.idle(),
                "steps": [
                    {"component": "salt", "action": "recovery_stop_cold_pump", "detail": stop},
                    {"component": "receiver", "action": "recovery_return_to_cold", "detail": receiver},
                ],
                "violations": [] if self._salt.cold_pump.idle() else ["pumps_not_idle"],
            }
        stowed = self._field.stow_all()
        removed = self._field.clear_durable_irradiance()
        return {
            "ok": True,
            "steps": [
                {"component": "field", "action": "recovery_stow_all", "detail": {"mirrors": stowed}},
                {"component": "field", "action": "recovery_clear_irradiance", "detail": {"removed": removed}},
            ],
            "violations": [],
        }


def _earlier_stage(first: str, second: str) -> str:
    return first if STAGES.index(first) <= STAGES.index(second) else second


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stage_views(checkpoint: StartupCheckpoint | None) -> list[dict]:
    views = []
    for index, stage in enumerate(STAGES):
        completed = checkpoint is not None and stage in checkpoint.completed_stages
        interrupted = checkpoint is not None and checkpoint.interrupted_stage == stage and checkpoint.status != "complete"
        views.append(
            {
                "name": stage,
                "index": index,
                "policy": STAGE_POLICY[stage],
                "completed": completed,
                "interrupted": interrupted,
                "steps_completed": [] if checkpoint is None else list(checkpoint.steps_completed.get(stage, [])),
            }
        )
    return views
