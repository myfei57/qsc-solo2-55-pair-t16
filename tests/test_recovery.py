"""启动断点续做、参数核对（迁移重做）与有序回退。"""

from __future__ import annotations

import pytest

from cspplant.app import build_plant
from cspplant.config import default_config
from cspplant.console.api import Api
from cspplant.errors import InterlockError


class _Crash(Exception):
    """模拟掉电：检查点已落盘，进程直接没了。"""


def _crash_after(platform, count: int) -> None:
    """让平台在第 ``count`` 步落盘后"掉电"。"""
    calls: list[tuple[str, str]] = []

    def observer(phase: str, step: str) -> None:
        calls.append((phase, step))
        if len(calls) >= count:
            raise _Crash()

    platform.recovery.step_observer = observer


def _clear_crash(platform) -> None:
    platform.recovery.step_observer = None


def test_fresh_run_completes_and_records_checkpoint(platform):
    platform.start()
    report = platform.start_plant(800.0, 30.0, 720.0)
    assert report["ok"] is True
    assert report["stage"] == "done"
    assert report["load_mw"] == pytest.approx(30.0)

    status = platform.recovery_status()
    assert status["interrupted"] is False
    checkpoint = status["checkpoint"]
    assert checkpoint["state"] == "completed"
    phases = {phase["name"]: phase["state"] for phase in checkpoint["phases"]}
    assert set(phases) == {"migration", "tracking", "circulation", "receiver", "steam"}
    assert all(state == "done" for state in phases.values())


def test_fresh_run_with_bad_params_fails_at_migration(platform):
    platform.start()
    report = platform.start_plant(5000.0, 30.0, 720.0)
    assert report["ok"] is False
    assert report["stage"] == "migration"
    assert any(item.startswith("param_check:") for item in report["violations"])


def test_resume_after_crash_skips_completed_steps(platform):
    platform.start()
    _crash_after(platform, 6)  # 迁移 + 追日完成，循环刚启泵
    with pytest.raises(_Crash):
        platform.start_plant(800.0, 30.0, 720.0)
    _clear_crash(platform)

    status = platform.recovery_status()
    assert status["interrupted"] is True
    phases = {phase["name"]: phase["state"] for phase in status["checkpoint"]["phases"]}
    assert phases["migration"] == "done"
    assert phases["tracking"] == "done"
    assert phases["circulation"] == "running"

    starts_before = platform.salt.cold_pump.starts()
    report = platform.recover()
    assert report["ok"] is True
    assert report["recovered"] is True
    assert report["recovery"]["outcome"] == "resume"
    # 已完成的步骤不重复执行：泵没有再启一次
    assert platform.salt.cold_pump.starts() == starts_before
    skipped = [step for step in report["steps"] if step["skipped"]]
    assert any(step["phase"] == "tracking" for step in skipped)
    assert platform.recovery.summary()["state"] == "completed"


def test_restart_reads_checkpoint_and_resumes(platform, clock):
    platform.start()
    _crash_after(platform, 4)  # 迁移完成，追日做到一半
    with pytest.raises(_Crash):
        platform.start_plant(800.0, 30.0, 720.0)

    restarted = build_plant(default_config(), clock=clock, storage_root=platform.store.root)
    status = restarted.recovery_status()
    assert status["interrupted"] is True
    assert status["plan"]["outcome"] == "resume"
    decisions = {item["phase"]: item["decision"] for item in status["plan"]["decisions"]}
    assert decisions["migration"] == "reverify"
    assert decisions["tracking"] == "resume"

    report = restarted.recover()
    assert report["ok"] is True
    assert report["recovery"]["outcome"] == "resume"
    assert restarted.recovery.summary()["state"] == "completed"
    actions = [entry["action"] for entry in restarted.audit.entries(limit=200)]
    assert "phase_decision" in actions
    assert "step_skipped" in actions
    assert "startup_complete" in actions


def test_changed_params_are_reverified_before_resume(platform):
    platform.start()
    _crash_after(platform, 6)
    with pytest.raises(_Crash):
        platform.start_plant(800.0, 30.0, 720.0)
    _clear_crash(platform)

    report = platform.recover({"load_mw": 20.0})
    assert report["ok"] is True
    recovery = report["recovery"]
    assert recovery["consistent"] is False
    assert any(item["key"] == "run.load_mw" for item in recovery["changed"])
    assert report["load_mw"] == pytest.approx(20.0)
    # 迁移通过后检查点换成新参数，不带着旧假设往下走
    checkpoint = platform.recovery_status()["checkpoint"]
    assert checkpoint["snapshot"]["run"]["load_mw"] == 20.0
    actions = [entry["action"] for entry in platform.audit.entries(limit=200)]
    assert "migration_verified" in actions


def test_incompatible_params_trigger_ordered_rollback(platform):
    platform.start()
    _crash_after(platform, 9)  # 迁移 + 追日 + 循环完成，受热与建负荷未做
    with pytest.raises(_Crash):
        platform.start_plant(800.0, 30.0, 720.0)
    _clear_crash(platform)

    report = platform.recover({"dni_w_m2": 5000.0})  # 超出辐照可信区间
    assert report["ok"] is False
    assert report["recovery"]["outcome"] == "rollback"
    rollback = [step["phase"] for step in report["steps"] if step.get("rollback")]
    assert rollback == ["circulation", "tracking"]  # 按相反顺序退干净
    assert platform.salt.cold_pump.idle() is True
    assert platform.salt.pumps_running() == 0
    assert platform.recovery.summary()["state"] == "rolled_back"
    actions = [entry["action"] for entry in platform.audit.entries(limit=200)]
    assert "migration_rejected" in actions
    assert "rollback_phase" in actions
    assert "startup_rolled_back" in actions


def test_recover_after_completion_is_noop(platform):
    platform.start()
    report = platform.start_plant(800.0, 30.0, 720.0)
    assert report["ok"] is True
    again = platform.recover()
    assert again["recovered"] is False
    assert again["reason"] == "no_interrupted_run"


def test_start_plant_refuses_while_interrupted(platform):
    platform.start()
    _crash_after(platform, 3)
    with pytest.raises(_Crash):
        platform.start_plant(800.0, 30.0, 720.0)
    _clear_crash(platform)
    with pytest.raises(InterlockError):
        platform.start_plant(800.0, 30.0, 720.0)


def test_effect_present_steps_are_not_reexecuted(platform):
    platform.start()
    platform.salt.start_cold()
    starts = platform.salt.cold_pump.starts()
    report = platform.start_plant(800.0, 30.0, 720.0)
    assert report["ok"] is True
    assert platform.salt.cold_pump.starts() == starts
    entry = [step for step in report["steps"] if step["step"] == "start_cold_pump"][0]
    assert entry["skipped"] is True
    assert entry["skip_reason"] == "现场效果已在"


def test_console_exposes_recovery(platform):
    platform.start()
    _crash_after(platform, 4)
    with pytest.raises(_Crash):
        platform.start_plant(800.0, 30.0, 720.0)
    _clear_crash(platform)

    api = Api(platform)
    status_code, payload = api.handle("GET", "/api/recovery", {}, {})
    assert status_code == 200
    assert payload["interrupted"] is True
    assert payload["plan"]["outcome"] == "resume"

    status_code, payload = api.handle("POST", "/api/recovery/run", {}, {})
    assert status_code == 200
    assert payload["ok"] is True

    status_code, payload = api.handle("GET", "/api/recovery", {}, {})
    assert status_code == 200
    assert payload["interrupted"] is False
    assert payload["checkpoint"]["state"] == "completed"


def test_recovery_events_replay_across_restart(platform, clock):
    platform.start()
    _crash_after(platform, 4)
    with pytest.raises(_Crash):
        platform.start_plant(800.0, 30.0, 720.0)
    _clear_crash(platform)
    platform.recover()

    restarted = build_plant(default_config(), clock=clock, storage_root=platform.store.root)
    actions = [record["action"] for record in restarted.audit.replay_from_journal()]
    assert "phase_decision" in actions
    assert "migration_verified" in actions
    assert "step_skipped" in actions
    assert "startup_complete" in actions
