"""命名空间、时钟、事件总线、标识与温度预估。"""

from __future__ import annotations

import pytest

from cspplant.clock import ManualClock, SystemClock
from cspplant.errors import NamespaceError, PlantError
from cspplant.eventbus import Event, EventBus
from cspplant.ids import IdFactory
from cspplant.ns.registry import Device, NamespaceRegistry
from cspplant.ns.topology import build_default_topology
from cspplant.ns.zones import SafeZone, ZoneRegistry
from cspplant.telemetry import Sample, TrendBuffer
from cspplant.temp.ambient import AmbientModel
from cspplant.temp.profile import TemperatureProfile
from cspplant.temp.solar import ClearSkyProfile
from cspplant.temp.temp import build_report
from cspplant.config import default_config


def test_manual_clock_advances_both_axes():
    clock = ManualClock(start=1000.0)
    assert clock.now() == pytest.approx(1000.0)
    clock.sleep(5.0)
    assert clock.now() == pytest.approx(1005.0)
    assert clock.monotonic() == pytest.approx(5.0)
    with pytest.raises(ValueError):
        clock.advance(-1.0)
    assert clock.utc_iso().endswith("Z")


def test_system_clock_reports_real_time():
    clock = SystemClock()
    assert clock.now() > 0
    assert clock.monotonic() >= 0
    clock.sleep(0.0)


def test_namespace_registry_and_topology():
    registry = NamespaceRegistry("field1")
    registry.register("tower", "receiver", "receiver")
    with pytest.raises(NamespaceError):
        registry.register("tower", "receiver", "receiver")
    assert registry.resolve("tower.receiver").kind == "receiver"
    assert registry.by_kind("receiver")[0].address == "tower.receiver"
    assert registry.describe()["count"] == 1
    with pytest.raises(NamespaceError):
        registry.resolve("tower.nope")
    assert "field1" == registry.unit
    assert Device("a", "b", "c").address == "a.b"

    topology = build_default_topology()
    verify = topology.verify({"irradiance"})
    assert "tracking" not in verify["missing"]
    assert verify["missing"]["receiver"] == ["tracking"]
    assert topology.downstream("irradiance") == ["tracking", "receiver", "salt", "steam", "audit"]
    assert topology.stage("steam").label == "蒸汽"


def test_zone_registry_covers_points():
    zones = ZoneRegistry()
    zones.add(SafeZone("maint", 0.0, 100.0, 20.0, "maintenance"))
    with pytest.raises(NamespaceError):
        zones.add(SafeZone("maint", 1.0, 1.0, 5.0))
    with pytest.raises(NamespaceError):
        zones.add(SafeZone("bad", 0.0, 0.0, 0.0))
    assert zones.names() == ["maint"]
    assert zones.covering(5.0, 100.0)[0].name == "maint"
    assert zones.covering(50.0, 100.0) == []
    assert zones.get("maint").contains(0.0, 119.0)
    assert zones.describe()["count"] == 1
    assert zones.remove("maint") is True
    assert zones.remove("maint") is False
    with pytest.raises(NamespaceError):
        zones.get("maint")


def test_event_bus_publish_and_history():
    bus = EventBus(history_limit=3)
    seen: list[str] = []
    unsubscribe = bus.subscribe("field", lambda event: seen.append(event.action))
    bus.subscribe("*", lambda event: seen.append(f"*{event.action}"))
    assert bus.publish(Event.of("field", "field", "aim", dni=800.0)) == 2
    assert seen == ["aim", "*aim"]
    unsubscribe()
    bus.publish(Event.of("field", "field", "stow"))
    assert seen[-1] == "*stow"
    assert len(bus.history()) == 2
    assert [event.action for event in bus.history("field")] == ["aim", "stow"]


def test_id_factory_produces_unique_prefixed_ids():
    factory = IdFactory("op")
    first = factory.next()
    second = factory.next()
    assert first.startswith("op-") and second.startswith("op-")
    assert first != second
    assert factory.issued() == 2
    assert factory.prefix == "op"
    with pytest.raises(ValueError):
        IdFactory("")


def test_trend_buffer_limits_and_gap_filling():
    buffer = TrendBuffer(limit=3, period_s=5.0)
    for index in range(5):
        buffer.append("temperature", float(index * 5), float(index))
    assert len(buffer.series("temperature", limit=10)) == 3
    assert buffer.latest("temperature").value == pytest.approx(4.0)
    filled = buffer.fill_gaps(
        "temperature", [Sample(0.0, 0.0), Sample(15.0, 3.0)]
    )
    assert [sample.ts for sample in filled] == [0.0, 5.0, 10.0, 15.0]
    assert buffer.snapshot(limit=1)["temperature"]["latest"]["value"] == pytest.approx(4.0)
    assert buffer.signals() == ["temperature"]
    assert buffer.period_s == pytest.approx(5.0)


def test_plant_error_carries_component():
    error = PlantError("失败", component="salt")
    assert error.as_record()["component"] == "salt"
    assert str(error) == "失败"


def test_temperature_profile_and_report():
    config = default_config()
    solar = ClearSkyProfile(peak_dni_w_m2=950.0)
    ambient = AmbientModel(min_c=8.0, max_c=34.0)
    profile = TemperatureProfile(config.receiver, solar, ambient, area_m2 := 55_350.0, 0.94)
    assert profile.power_kw(1000.0) > 0
    points = profile.predict(600.0, 58.0, horizon_min=20.0, step_min=10.0)
    assert len(points) == 3
    assert points[0].derate_margin_c == pytest.approx(config.receiver.temp_derate_c - points[0].outlet_c)
    assert profile.minutes_to_derate(600.0, 58.0) is not None
    assert solar.energy_remaining_kj_m2(600.0) > 0
    assert solar.day_length_min > 0
    assert ambient.mean_c() == pytest.approx(21.0)

    report = build_report(config.receiver, 590.0, False)
    assert report.at_target is True
    assert report.margin_to_max_c == pytest.approx(config.receiver.temp_max_c - 590.0)
    assert report.as_dict()["derated"] is False
    del area_m2
