"""清洗循环与回水回收顺序。"""

from __future__ import annotations

import pytest

from cspplant.config import default_config
from cspplant.errors import InterlockError
from cspplant.wash.cycle import WashCycle
from cspplant.wash.drain import DrainSystem
from cspplant.wash.truck import WashTruck


def test_truck_rejects_wash_without_water():
    limits = default_config().wash
    truck = WashTruck(limits, water_m3=0.0)
    truck.arrive("m001")
    with pytest.raises(InterlockError):
        truck.start_wash()


def test_drain_refuses_to_stop_before_tray_is_empty():
    limits = default_config().wash
    drain = DrainSystem(limits)
    drain.receive(0.5)
    drain.open_drain()
    with pytest.raises(InterlockError):
        drain.stop_pump()
    assert drain.premature_stops() == 1
    for _ in range(200):
        drain.step(1.0)
    assert drain.drained() is True
    assert drain.stop_pump() is True
    assert drain.stops() == 1
    assert drain.recovered_m3() == pytest.approx(0.5)


def test_wash_cycle_runs_in_order():
    limits = default_config().wash
    truck = WashTruck(limits)
    drain = DrainSystem(limits)
    report = WashCycle().run(truck, drain, "m001")
    assert report.ok is True
    assert report.violations == []
    actions = [step["action"] for step in report.steps]
    assert actions.index("arrive") < actions.index("spray")
    assert actions.index("spray") < actions.index("open_drain")
    assert actions.index("open_drain") < actions.index("stop_return_pump")
    assert report.recovered_m3 > 0.0
    assert truck.washes() == 1


def test_platform_wash_stows_window_mirrors(platform):
    platform.start()
    platform.track(800.0, 720.0)
    report = platform.wash(["m021", "m022"], "maint-north")
    assert report["ok"] is True
    assert report["washed"] == 2
    assert platform.drain.premature_stops() == 0
    assert platform.truck.washes() == 2
    assert platform.mirrors_in_window() == []
