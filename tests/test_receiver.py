"""吸热器流量门控、通盐、受热与保护。"""

from __future__ import annotations

import pytest

from cspplant.config import default_config
from cspplant.errors import InterlockError
from cspplant.receiver.derate import DerateController
from cspplant.receiver.flow import FlowGate
from cspplant.receiver.header import SaltHeader
from cspplant.receiver.receiver import AT_POWER, CIRCULATING, COLD, DERATED, TRIPPED, Receiver
from cspplant.receiver.sensor import TemperatureSensor
from cspplant.receiver.thermal import ThermalModel
from cspplant.receiver.threshold import ThresholdModel
from cspplant.receiver.valve import InletValve


def build_receiver(limits=None) -> Receiver:
    config = default_config()
    receiver_limits = limits or config.receiver
    return Receiver(
        receiver_limits,
        InletValve(receiver_limits),
        FlowGate(receiver_limits),
        TemperatureSensor(receiver_limits.ambient_c),
        ThermalModel(receiver_limits),
        DerateController(receiver_limits),
        SaltHeader(config.salt),
        ThresholdModel(config.salt),
    )


def test_flow_gate_needs_continuous_hold():
    receiver = build_receiver()
    limits = receiver.limits
    receiver.verify_flow(limits.flow_min_kg_s + 5.0, limits.flow_confirm_s / 2.0)
    assert receiver.flow_confirmed() is False
    receiver.verify_flow(limits.flow_min_kg_s - 1.0, 1.0)
    assert receiver.gate.hold_s() == 0.0
    assert receiver.gate.resets() == 1
    receiver.verify_flow(limits.flow_min_kg_s + 5.0, limits.flow_confirm_s)
    assert receiver.flow_confirmed() is True
    assert receiver.state() == CIRCULATING


def test_admit_requires_confirmed_flow():
    receiver = build_receiver()
    with pytest.raises(InterlockError):
        receiver.admit()
    receiver.verify_flow(receiver.limits.flow_min_kg_s + 5.0, receiver.limits.flow_confirm_s)
    assert receiver.admit() == "admitted"
    assert receiver.valve.target_pct() == pytest.approx(100.0)


def test_heat_requires_admitted_header():
    receiver = build_receiver()
    with pytest.raises(InterlockError):
        receiver.heat(1000.0, 60.0, 1.0)
    assert receiver.state() == COLD


def test_heat_raises_temperature_toward_steady_state():
    receiver = build_receiver()
    limits = receiver.limits
    receiver.verify_flow(limits.flow_min_kg_s + 16.0, limits.flow_confirm_s)
    receiver.admit()
    for _ in range(600):
        report = receiver.heat(25_000.0, limits.flow_min_kg_s + 16.0, 1.0)
    assert report["ok"] is True
    assert receiver.state() == AT_POWER
    assert 520.0 < receiver.output_temperature_c() < 600.0
    assert receiver.thermal.dry_steps() == 0


def test_low_flow_puts_ceiling_to_zero():
    receiver = build_receiver()
    limits = receiver.limits
    receiver.verify_flow(limits.flow_min_kg_s + 16.0, limits.flow_confirm_s)
    receiver.admit()
    receiver.threshold.calibrate(58.0)
    assert receiver.ceiling_pct(58.0) == pytest.approx(100.0)
    assert receiver.ceiling_pct(20.0) == pytest.approx(0.0)


def test_derate_ceiling_interpolates_between_band_and_limit():
    receiver = build_receiver()
    limits = receiver.limits
    assert receiver.derate.ceiling_pct(limits.temp_derate_c - 1.0) == pytest.approx(100.0)
    mid = (limits.temp_derate_c + limits.temp_max_c) / 2.0
    assert receiver.derate.ceiling_pct(mid) == pytest.approx(50.0)
    assert receiver.derate.ceiling_pct(limits.temp_max_c) == pytest.approx(0.0)


def test_derate_controller_has_hysteresis():
    receiver = build_receiver()
    limits = receiver.limits
    assert receiver.derate.evaluate(limits.temp_derate_c).active is True
    receiver.derate.evaluate(limits.temp_derate_c - 1.0)
    assert receiver.derate.active() is True
    receiver.derate.evaluate(limits.temp_derate_c - 6.0)
    assert receiver.derate.active() is False
    assert receiver.derate.entries() == 1
    assert receiver.derate.clears() == 1


def test_overtemperature_trips_and_reset_requires_latch_cleared():
    receiver = build_receiver()
    limits = receiver.limits
    receiver.verify_flow(limits.flow_min_kg_s + 16.0, limits.flow_confirm_s)
    receiver.admit()
    for _ in range(200):
        receiver.heat(120_000.0, 1.0, 1.0)
        if receiver.state() == TRIPPED:
            break
    assert receiver.state() == TRIPPED
    assert receiver.trip_reason() == "overtemperature"
    with pytest.raises(InterlockError):
        receiver.reset(latch_cleared=False)
    assert receiver.reset(latch_cleared=True) == COLD


def test_thermal_model_carries_flow_away():
    receiver = build_receiver()
    limits = receiver.limits
    receiver.verify_flow(limits.flow_min_kg_s + 16.0, limits.flow_confirm_s)
    receiver.admit()
    dry = build_receiver()
    dry.verify_flow(limits.flow_min_kg_s + 16.0, limits.flow_confirm_s)
    dry.admit()
    for _ in range(200):
        receiver.heat(25_000.0, 58.0, 1.0)
        dry.heat(25_000.0, 0.0, 1.0)
    assert dry.output_temperature_c() > receiver.output_temperature_c() + 100.0
    assert dry.thermal.dry_steps() == 200


def test_header_admit_and_overpressure():
    config = default_config()
    header = SaltHeader(config.salt)
    with pytest.raises(InterlockError):
        header.admit(False)
    assert header.admit(True) == "admitted"
    for _ in range(60):
        header.update(config.salt.flow_baseline_kg_s, 1, 1.0)
    assert header.overpressure() is False
    for _ in range(60):
        header.update(config.salt.flow_baseline_kg_s, 2, 1.0)
    assert header.overpressure() is True
    assert header.depressurise(60.0) < config.salt.header_pressure_max_mpa


def test_threshold_tracks_calibration_epoch():
    model = ThresholdModel(default_config().salt)
    before = model.threshold_kg_s()
    model.calibrate(70.0)
    assert model.epoch == 1
    assert model.threshold_kg_s() > before
    assert model.evaluate(70.0)["ok"] is True
    assert model.evaluate(30.0)["ok"] is False
