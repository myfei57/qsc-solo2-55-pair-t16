"""蒸汽旁路顺序、负荷斜率与压力。"""

from __future__ import annotations

import pytest

from cspplant.config import default_config
from cspplant.errors import InterlockError
from cspplant.steam.bypass import BypassValve
from cspplant.steam.pressure import SteamPressure
from cspplant.steam.turbine import Turbine
from cspplant.steam.turbine_load import LoadRamp


def build_turbine() -> Turbine:
    limits = default_config().steam
    return Turbine(limits, LoadRamp(limits), BypassValve(limits))


def test_bypass_rejects_double_open():
    valve = BypassValve(default_config().steam)
    valve.open(10.0)
    with pytest.raises(InterlockError):
        valve.open(11.0)
    assert valve.held_open_s(15.0) == pytest.approx(5.0)
    valve.close()
    with pytest.raises(InterlockError):
        valve.close()


def test_shed_opens_bypass_before_closing_main_stop():
    turbine = build_turbine()
    turbine.raise_load(20.0)
    assert turbine.main_stop_open() is True
    report = turbine.shed(0.0, 100.0)
    assert report.ok is True
    actions = [step["action"] for step in report.steps]
    assert actions.index("open_bypass") < actions.index("lower_load")
    assert actions.index("lower_load") < actions.index("close_main_stop")
    assert turbine.bypass.is_open() is True
    assert turbine.main_stop_open() is False
    assert turbine.sheds() == 1


def test_shed_below_current_load_is_required():
    turbine = build_turbine()
    turbine.raise_load(10.0)
    report = turbine.shed(20.0, 100.0)
    assert report.ok is False
    assert "shed_target_not_lower" in report.violations


def test_load_ramp_respects_rate_and_ceiling():
    limits = default_config().steam
    ramp = LoadRamp(limits)
    ramp.request(limits.load_max_mw * 10)
    assert ramp.target_mw() == pytest.approx(limits.load_max_mw)
    assert ramp.step(1.0) == pytest.approx(limits.load_rate_mw_s)
    ramp.settle()
    assert ramp.current_mw() == pytest.approx(limits.load_max_mw)


def test_turbine_trip_is_bypass_first():
    turbine = build_turbine()
    turbine.raise_load(12.0)
    report = turbine.trip(50.0)
    assert report.ok is True
    assert turbine.bypass.is_open() is True
    assert turbine.main_stop_open() is False
    assert turbine.ramp.current_mw() == pytest.approx(0.0)
    assert turbine.trips() == 1


def test_pressure_moves_with_net_steam():
    limits = default_config().steam
    pressure = SteamPressure(limits)
    start = pressure.value_mpa()
    pressure.update(20.0, 5.0, 0.0, 10.0)
    assert pressure.value_mpa() > start
    pressure.update(0.0, 5.0, 0.0, 10.0)
    assert pressure.value_mpa() < start + 100.0
    assert pressure.updates() == 2


def test_steam_system_settles_pressure_under_load(online):
    online.clock.advance(1.0)
    for _ in range(400):
        online.cycle(dt=2.0)
    status = online.status()["steam"]
    assert status["settled"] is True
    assert status["load_mw"] > 20.0
    assert status["main_stop_open"] is True
    assert status["bypass"]["open"] is False


def test_disturbance_is_visible_and_recoverable(online):
    disturbed = online.disturb_steam(1.5)
    assert disturbed["settled"] is False
    for _ in range(400):
        online.cycle(dt=2.0)
    assert online.status()["steam"]["settled"] is True
