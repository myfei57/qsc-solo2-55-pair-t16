"""盐泵、水力、切换顺序与标定。"""

from __future__ import annotations

import pytest

from cspplant.config import default_config
from cspplant.errors import InterlockError
from cspplant.salt.hydraulic import HydraulicLoop
from cspplant.salt.pump import SaltPump
from cspplant.salt.tank import SaltTank


def test_pump_ramps_at_configured_rate():
    limits = default_config().salt
    pump = SaltPump("cold-pump", limits)
    pump.start()
    assert pump.flow_kg_s() == 0.0
    assert pump.step(1.0) == pytest.approx(limits.pump_ramp_kg_s2)
    pump.settle()
    assert pump.flow_kg_s() == pytest.approx(limits.flow_baseline_kg_s)
    assert pump.at_target() is True
    pump.stop()
    pump.settle()
    assert pump.idle() is True
    assert pump.starts() == 1 and pump.stops() == 1


def test_pump_rejects_double_start():
    pump = SaltPump("cold-pump", default_config().salt)
    pump.start()
    with pytest.raises(InterlockError):
        pump.start()


def test_two_pumps_are_opposed_in_hydraulics():
    limits = default_config().salt
    cold = SaltPump("cold-pump", limits)
    hot = SaltPump("hot-pump", limits)
    loop = HydraulicLoop(limits)
    cold.start()
    for _ in range(120):
        loop.update([cold, hot], 1.0)
    single = loop.net_flow_kg_s()
    hot.start()
    for _ in range(120):
        loop.update([cold, hot], 1.0)
    assert loop.net_flow_kg_s() < single * 1.4
    assert loop.opposed_steps() > 0
    assert loop.updates() == 240


def test_tank_levels_and_bounds():
    tank = SaltTank("cold-tank", 1000.0, 500.0, min_pct=20.0, max_pct=80.0)
    assert tank.level_pct() == pytest.approx(50.0)
    tank.draw(100.0)
    assert tank.level_kg() == pytest.approx(400.0)
    tank.fill(1000.0)
    assert tank.level_pct() == pytest.approx(80.0)
    assert tank.at_max() is True
    with pytest.raises(InterlockError):
        tank.fill(10.0)


def test_changeover_stops_cold_before_starting_hot(platform):
    platform.start()
    platform.track(800.0, 720.0)
    platform.circulate()
    report = platform.changeover()
    assert report["ok"] is True
    assert report["violations"] == []
    assert report["max_pumps_running"] == 1
    assert report["peak_pressure_mpa"] < platform.config.salt.header_pressure_max_mpa
    actions = [step["action"] for step in report["steps"]]
    assert actions.index("stop_cold_pump") < actions.index("start_hot_pump")
    assert platform.salt.cold_pump.running() is False
    assert platform.salt.hot_pump.running() is True


def test_changeover_requires_running_cold_pump(platform):
    platform.start()
    report = platform.changeover()
    assert report["ok"] is False
    assert "cold_pump_not_running" in report["violations"]


def test_start_hot_pump_rejected_while_cold_running(platform):
    platform.start()
    platform.salt.start_cold()
    with pytest.raises(InterlockError):
        platform.salt.start_hot()


def test_calibration_moves_interlock_threshold(platform):
    platform.start()
    platform.salt.start_cold()
    platform.salt.cold_pump.settle()
    before = platform.salt.threshold_kg_s()
    record = platform.calibrate_flow(72.0, "shift-lead")
    assert record["epoch"] == 1
    assert record["baseline_kg_s"] == pytest.approx(72.0)
    assert platform.salt.threshold_kg_s() > before
    assert platform.receiver.threshold.threshold_kg_s() == pytest.approx(platform.salt.threshold_kg_s())


def test_salt_levels_persist_across_restart(platform, clock):
    from cspplant.app import build_plant

    platform.start()
    platform.track(800.0, 720.0)
    platform.circulate()
    for _ in range(60):
        platform.cycle(1.0)
    cold_pct = platform.salt.cold_tank.level_pct()
    hot_pct = platform.salt.hot_tank.level_pct()

    restarted = build_plant(default_config(), clock=clock, storage_root=platform.store.root)
    assert restarted.salt.cold_tank.level_pct() == pytest.approx(cold_pct, abs=1e-6)
    assert restarted.salt.hot_tank.level_pct() == pytest.approx(hot_pct, abs=1e-6)
