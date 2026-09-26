"""平台装配、控制循环、并发仲裁与跨重启一致性。"""

from __future__ import annotations

import pytest

from cspplant.app import build_plant
from cspplant.config import default_config
from cspplant.errors import InterlockError


def test_cycle_regulates_temperature_and_pressure(online):
    for _ in range(300):
        online.cycle(dt=2.0)
    status = online.status()
    assert status["receiver"]["state"] == "at_power"
    assert status["receiver"]["flow_confirmed"] is True
    assert 400.0 < status["receiver"]["temperature_c"] < online.config.receiver.temp_derate_c
    assert status["steam"]["settled"] is True
    assert status["salt"]["net_flow_kg_s"] == pytest.approx(online.config.salt.flow_baseline_kg_s)


def test_cycle_before_admit_does_not_heat(platform):
    platform.start()
    platform.track(800.0, 720.0)
    report = platform.cycle(dt=1.0)
    assert report["heat"]["skipped"] == "not_admitted"
    assert report["state"] == "cold"


def test_concurrency_probe_converges_to_interlock_ceiling(platform):
    platform.start()
    outcome = platform.concurrency_probe(rounds=16)
    assert outcome["effective_pct"] == pytest.approx(outcome["ceiling_pct"])
    assert outcome["clamped"] is True
    assert outcome["committed_on_valve"] == pytest.approx(outcome["effective_pct"])
    assert set(outcome["writers"]) == {"auto_dispatch", "local_interlock"}


def test_dispatch_is_clamped_by_local_ceiling(platform):
    platform.start()
    platform.track(800.0, 720.0)
    platform.circulate()
    platform.receiver.derate.evaluate(platform.config.receiver.temp_max_c - 30.0)
    outcome = platform.dispatch(100.0, "auto")
    assert outcome["effective_pct"] < 100.0
    assert outcome["clamped"] is True
    assert platform.receiver.valve.target_pct() == pytest.approx(outcome["effective_pct"])


def test_low_flow_pushes_dispatch_to_zero(platform):
    platform.start()
    platform.track(800.0, 720.0)
    platform.circulate()
    platform.salt.cold_pump.stop()
    for _ in range(200):
        platform.salt.step(1.0)
    outcome = platform.dispatch(100.0, "auto")
    assert outcome["effective_pct"] == pytest.approx(0.0)
    assert platform.salt.flow_kg_s() == pytest.approx(0.0)


def test_audit_records_operations_and_rejections(platform):
    platform.start()
    platform.track(800.0, 720.0)
    platform.wash(["m021"], "maint-north")
    summary = platform.audit.summary()
    assert summary["total"] > 0
    assert "track_complete" in summary["by_action"]
    assert "wash_complete" in summary["by_action"]
    platform.field.open_window(["m031"], "maint-west", platform.clock.now())
    with pytest.raises(InterlockError):
        platform.field.open_window(["m041"], "maint-north", platform.clock.now())


def test_journal_replays_across_restart(platform, clock):
    platform.start()
    platform.track(800.0, 720.0)
    platform.trip("overtemperature")
    assert platform.audit.count("receiver_trip_complete") == 1

    restarted = build_plant(default_config(), clock=clock, storage_root=platform.store.root)
    assert restarted.latch.latched() is True
    assert restarted.latch.reason() == "overtemperature"
    assert restarted.status()["operations"] >= 2


def test_windows_survive_restart(platform, clock):
    platform.start()
    platform.track(800.0, 720.0)
    platform.field.open_window(["m021", "m062"], "maint-north", platform.clock.now())

    restarted = build_plant(default_config(), clock=clock, storage_root=platform.store.root)
    assert restarted.mirrors_in_window() == ["m021", "m062"]
    assert restarted.field.drive("m021").excluded is True
    assert restarted.field.drive("m021").elevation_deg == pytest.approx(0.0)


def test_snapshot_exposes_all_sections(online):
    snapshot = online.snapshot()
    for key in ("status", "topology", "namespaces", "zones", "heliostats", "rules", "trend"):
        assert key in snapshot
    assert snapshot["zones"]["count"] == 3
    assert snapshot["heliostats"]["count"] == 123
    assert snapshot["namespaces"]["count"] == 13


def test_trend_collects_signals_after_cycles(online):
    for _ in range(5):
        online.cycle(dt=5.0)
    samples = online.trend.series("receiver_temperature", limit=10)
    assert len(samples) == 5
    assert online.trend.latest("field_power_kw") is not None
    filled = online.trend.fill_gaps("receiver_temperature", samples)
    assert len(filled) >= len(samples)


def test_reset_state_clears_counters(platform):
    platform.start()
    platform.cycle(dt=1.0)
    assert platform.reset_state() is True
    assert platform.reset_state() is False


def test_note_error_writes_audit_entry(platform):
    platform.start()
    record = platform.note_error(InterlockError("联锁拒绝", component="receiver"))
    assert record["component"] == "receiver"
    assert platform.audit.summary()["by_severity"]["warning"] == 1
