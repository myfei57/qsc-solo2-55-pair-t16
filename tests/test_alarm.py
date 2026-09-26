"""告警规则、闩锁与升级。"""

from __future__ import annotations

import pytest

from cspplant.alarm.escalation import EscalationPolicy, LEVEL_NAMES
from cspplant.alarm.history import AlarmHistory
from cspplant.alarm.latch import FaultLatch
from cspplant.alarm.rules import ALARM, WARNING, AlarmRule, AlarmRules, default_rules
from cspplant.config import default_config
from cspplant.errors import InterlockError


def test_rules_evaluate_only_breached_signals():
    config = default_config()
    rules = AlarmRules(default_rules(config))
    triggered = rules.evaluate(
        {
            "receiver_temperature_c": config.receiver.temp_max_c + 5.0,
            "hot_tank_pct": 10.0,
        }
    )
    names = sorted(event.rule for event in triggered)
    assert names == ["receiver-derate", "receiver-overtemperature"]
    assert all(event.severity == ALARM for event in triggered if "overtemperature" in event.rule)


def test_rules_ignore_missing_signals():
    rules = AlarmRules([AlarmRule("x", "missing", 1.0)])
    assert rules.evaluate({"other": 100.0}) == []


def test_duplicate_rule_name_is_rejected():
    rules = AlarmRules([AlarmRule("x", "a", 1.0)])
    with pytest.raises(ValueError):
        rules.add(AlarmRule("x", "b", 2.0))


def test_latch_requires_cooldown_and_operator():
    latch = FaultLatch(default_config().alarm)
    latch.trip("receiver", "overtemperature", 100.0)
    assert latch.latched() is True
    assert latch.ready_to_reset(101.0) is False
    with pytest.raises(InterlockError):
        latch.reset(101.0, "operator")
    assert latch.rejections() == 1
    with pytest.raises(InterlockError):
        latch.reset(200.0, "")
    state = latch.reset(200.0, "operator")
    assert state.latched is False
    assert latch.resets() == 1


def test_latch_reset_without_trip_is_rejected():
    latch = FaultLatch(default_config().alarm)
    with pytest.raises(InterlockError):
        latch.reset(1.0, "operator")


def test_escalation_levels_follow_severity_and_count():
    policy = EscalationPolicy(default_config().alarm)
    assert policy.evaluate(0, WARNING).level == 0
    assert policy.evaluate(1, WARNING).level == 1
    assert policy.evaluate(1, ALARM).level == 2
    assert policy.evaluate(3, ALARM).level == 3
    assert policy.evaluate(9, WARNING).level == 2
    assert policy.evaluate(2, ALARM).name == LEVEL_NAMES[2]
    assert policy.window_s == default_config().alarm.escalation_window_s


def test_history_window_counting_and_order():
    rules = AlarmRules(default_rules(default_config()))
    history = AlarmHistory(limit=8)
    for index in range(6):
        history.record_all(float(index), rules.evaluate({"receiver_temperature_c": 999.0}))
    assert history.count() == 8
    recent = history.recent(8)
    stamps = [item["ts"] for item in recent]
    assert stamps == sorted(stamps, reverse=True)
    assert stamps[0] == pytest.approx(5.0)
    assert stamps[-1] == pytest.approx(2.0)
    assert history.occurrences("receiver-overtemperature", 2.0) == 4
    assert history.by_rule()["receiver-overtemperature"] == 4
    assert history.by_rule()["receiver-derate"] == 4


def test_platform_trip_sets_latch_and_blocks_focus(platform):
    platform.start()
    platform.track(800.0, 720.0)
    report = platform.trip("overtemperature")
    assert report["latched"] is True
    assert platform.latch.latched() is True
    assert platform.field.focusing_count() == 0
    blocked = platform.track(800.0, 720.0)
    assert blocked["mode"] == "stow"
    assert blocked["reason"] == "fault_latched"


def test_reset_after_cooldown_clears_latch(platform, clock):
    platform.start()
    platform.track(800.0, 720.0)
    platform.trip("overtemperature")
    early = platform.reset_trip("shift-lead")
    assert early["ok"] is False
    clock.advance(platform.config.alarm.latch_reset_delay_s + 1.0)
    ok = platform.reset_trip("shift-lead")
    assert ok["ok"] is True
    assert ok["state"] == "cold"
    assert platform.latch.latched() is False


def test_alert_levels_cover_every_rule(platform):
    platform.start()
    levels = platform.alert_levels()
    assert [item["rule"] for item in levels] == platform.rules.names()
    assert all(item["level"] == 0 for item in levels)
