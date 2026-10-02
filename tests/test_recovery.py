"""启动断点、按阶段恢复与参数核对。"""

from __future__ import annotations

import dataclasses

from cspplant.app import build_plant
from cspplant.config import default_config
from cspplant.sequence.recovery import (
    CIRCULATION,
    RECEIVER,
    STEAM,
    TRACKING,
    StartupRecovery,
)


def _tracking_checkpoint(platform, interrupted_stage: str, *, completed: list[str] | None = None):
    recovery = StartupRecovery(platform.store, platform.config, platform.clock.now)
    checkpoint = recovery.begin(dni_w_m2=800.0, load_mw=30.0, minute_of_day=720.0)
    completed = completed or []
    checkpoint.completed_stages = completed
    if TRACKING in completed:
        checkpoint.stages[TRACKING] = {
            "completed": True,
            "aimed": 123,
            "dni_w_m2": 800.0,
            "mode": "focus",
            "focuses": True,
        }
        checkpoint.steps_completed[TRACKING] = ["acquire", "persist_irradiance", "decide", "aim", "settle"]
    if CIRCULATION in completed:
        checkpoint.stages[CIRCULATION] = {
            "completed": True,
            "flow_kg_s": platform.config.salt.flow_baseline_kg_s,
            "header": "admitted",
        }
    if RECEIVER in completed:
        checkpoint.stages[RECEIVER] = {
            "completed": True,
            "flow_kg_s": platform.config.salt.flow_baseline_kg_s,
            "temperature_c": platform.config.receiver.temp_target_c,
            "state": "at_power",
        }
    checkpoint.interrupted_stage = interrupted_stage
    recovery.save(checkpoint)
    return checkpoint


def test_completed_startup_is_idempotent(platform):
    first = platform.start_plant(800.0, 30.0, 720.0)
    second = platform.start_plant(800.0, 30.0, 720.0)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["stage"] == "done"
    assert second["recovery"]["action"] == "done"
    assert platform.audit.count("startup_complete") == 1


def test_resume_decision_is_fixed_at_stage_level(platform, clock):
    platform.start()
    platform.track(800.0, 720.0)
    _tracking_checkpoint(platform, CIRCULATION, completed=[TRACKING])

    restarted = build_plant(default_config(), clock=clock, storage_root=platform.store.root)
    plan = restarted.startup_recovery.plan()

    assert plan["decision_unit"] == "stage"
    assert plan["interrupted_stage"] == CIRCULATION
    assert plan["stages"][0]["policy"] == "resume"
    assert plan["stages"][1]["policy"] == "rollback"
    assert plan["action"] == "rollback"
    assert plan["resume_stage"] == CIRCULATION


def test_tracking_resume_skips_completed_step(platform, clock):
    platform.start()
    recovery = StartupRecovery(platform.store, platform.config, platform.clock.now)
    checkpoint = recovery.begin(dni_w_m2=800.0, load_mw=30.0, minute_of_day=720.0)
    recovery.mark_step_done(
        checkpoint,
        TRACKING,
        "acquire",
        {"sample": {"accepted": True, "dni_w_m2": 800.0, "ts": 0.0}},
    )

    restarted = build_plant(default_config(), clock=clock, storage_root=platform.store.root)
    report = restarted.start_plant(800.0, 30.0, 720.0)

    assert report["ok"] is True
    assert restarted.field.meter.sample_count() == 0
    assert restarted.field.meter.persist_count() == 1
    assert any(step["action"] == "skip_completed_step" for step in report["steps"])


def test_receiver_interruption_rolls_back_in_order_then_continues(platform, clock):
    platform.start()
    platform.track(800.0, 720.0)
    platform.circulate()
    _tracking_checkpoint(platform, RECEIVER, completed=[TRACKING, CIRCULATION])

    restarted = build_plant(default_config(), clock=clock, storage_root=platform.store.root)
    report = restarted.start_plant(800.0, 30.0, 720.0)
    actions = [step["action"] for step in report["steps"]]

    assert report["ok"] is True
    assert actions.index("recovery_receiver_already_rolled_back") < actions.index("first_heat")
    assert actions.index("first_heat") < actions.index("raise_load")
    assert restarted.receiver.state() == "at_power"


def test_config_change_during_outage_is_rechecked_and_migration_rolls_back(platform, clock):
    platform.start()
    platform.track(800.0, 720.0)
    _tracking_checkpoint(platform, STEAM, completed=[TRACKING, CIRCULATION, RECEIVER])
    changed_config = dataclasses.replace(
        platform.config,
        steam=dataclasses.replace(platform.config.steam, load_max_mw=40.0),
    )

    restarted = build_plant(changed_config, clock=clock, storage_root=platform.store.root)
    plan = restarted.startup_recovery.plan()
    report = restarted.start_plant(800.0, 30.0, 720.0)

    assert plan["parameter_check"]["changed_sections"] == ["steam"]
    assert plan["rollback_to_stage"] == STEAM
    assert report["ok"] is True
    actions = [step["action"] for step in report["steps"]]
    assert "parameter_check" in actions
    assert "parameter_recheck" in actions
    assert actions.index("rollback_decision") < actions.index("raise_load")
