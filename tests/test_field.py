"""辐照落盘、追日策略、手动接管与避让区。"""

from __future__ import annotations

import pytest

from cspplant.app import build_plant
from cspplant.config import default_config
from cspplant.errors import InterlockError
from cspplant.field.strategy import FOCUS, STANDBY, STOW


def test_tracking_waits_for_durable_irradiance(platform):
    """内存里有读数但没落盘时，镜场只能待机。"""

    platform.start()
    sample = platform.field.acquire(760.0, platform.clock.now())
    assert sample.accepted is True
    assert platform.field.durable_irradiance() == 0.0

    report = platform.field.track(platform.sun_at(720.0), platform.clock.now())
    assert report["mode"] == STANDBY
    assert report["reason"] == "irradiance_not_durable"
    assert report["aimed"] == 0

    platform.field.persist_irradiance()
    focused = platform.track(760.0, 720.0)
    assert focused["mode"] == FOCUS
    assert focused["aimed"] == len(platform.heliostats.names())


def test_tracking_uses_persisted_value_after_restart(platform, clock):
    """重启后按已落盘值判定，不用内存里那份更新的读数。"""

    platform.start()
    platform.track(820.0, 720.0)
    assert platform.field.durable_irradiance() == pytest.approx(820.0)

    restarted = build_plant(default_config(), clock=clock, storage_root=platform.store.root)
    assert restarted.field.durable_irradiance() == pytest.approx(820.0)
    assert restarted.field.meter.current() == pytest.approx(820.0)

    # 只更新内存、不落盘：判定必须停在待机。
    restarted.field.acquire(300.0, clock.now())
    report = restarted.field.track(restarted.sun_at(720.0), clock.now())
    assert report["mode"] == STANDBY
    assert report["reason"] == "irradiance_not_durable"

    # 走完整编排（先落盘）之后才允许聚焦，且用的是新值。
    focused = restarted.track(300.0, 720.0)
    assert focused["mode"] == STANDBY
    assert focused["reason"] == "below_focus_threshold"
    assert restarted.field.durable_irradiance() == pytest.approx(300.0)


def test_irradiance_rejects_out_of_range_reading(platform):
    platform.start()
    platform.field.acquire(800.0, platform.clock.now())
    rejected = platform.field.acquire(5000.0, platform.clock.now())
    assert rejected.accepted is False
    assert rejected.reason == "out_of_range"
    assert platform.field.meter.rejected() == 1
    assert platform.field.meter.current() == pytest.approx(800.0)


def test_stale_irradiance_blocks_focus(platform, clock):
    platform.start()
    platform.track(820.0, 720.0)
    clock.advance(default_config().irradiance.stale_after_s + 5.0)
    report = platform.field.track(platform.sun_at(720.0), clock.now())
    assert report["mode"] == STANDBY
    assert report["reason"] == "irradiance_not_durable"


def test_latch_stows_the_field(platform):
    platform.start()
    platform.track(820.0, 720.0)
    report = platform.field.track(platform.sun_at(720.0), platform.clock.now(), latched=True)
    assert report["mode"] == STOW
    assert platform.field.focusing_count() == 0
    assert platform.field.thermal_power_kw() == 0.0


def test_manual_aim_survives_auto_tracking(platform):
    platform.start()
    platform.track(800.0, 720.0)
    lease = platform.field.manual_aim("m021", 200.0, 30.0, "operator-1", platform.clock.now())
    assert lease.azimuth_deg == pytest.approx(200.0)

    drive = platform.field.drive("m021")
    assert drive.manual is True
    with pytest.raises(InterlockError):
        drive.aim(platform.field.stow_angles_for("m021"))

    platform.track(800.0, 720.0)
    assert drive.azimuth_deg == pytest.approx(200.0, abs=1e-6)


def test_manual_lease_expires(platform, clock):
    platform.start()
    platform.field.manual_aim("m021", 195.0, 30.0, "operator-1", clock.now(), hold_s=30.0)
    assert platform.field.manual.count() == 1
    clock.advance(31.0)
    expired = platform.field.expire_manual(clock.now())
    assert expired == ["m021"]
    assert platform.field.drive("m021").manual is False


def test_zone_refresh_picks_posture_per_landing_point(platform):
    """避让区覆盖落点的镜子收起，不覆盖的只离焦。"""

    platform.start()
    platform.track(800.0, 720.0)
    window = platform.field.open_window(["m021", "m062"], "maint-north", platform.clock.now())
    assert set(window.mirrors) == {"m021", "m062"}

    covered = platform.field.drive("m021")
    uncovered = platform.field.drive("m062")
    assert covered.excluded is True and uncovered.excluded is True
    assert covered.elevation_deg == pytest.approx(0.0)
    assert uncovered.elevation_deg > 0.0
    assert platform.field.guard.vetoes() == 1

    platform.field.close_window(platform.clock.now())
    assert platform.field.drive("m021").excluded is False


def test_sweep_planner_batches_and_windows(platform):
    planner = platform.field.sweeps
    batches = planner.plan(["m003", "m001", "m002", "m001"])
    assert batches[0][:3] == ["m001", "m002", "m003"]
    window = planner.begin(["m001"], "maint-north", 10.0)
    assert window.open is True
    with pytest.raises(InterlockError):
        planner.begin(["m002"], "maint-west", 11.0)
    closed = planner.finish(20.0)
    assert closed.closed_at == pytest.approx(20.0)
    assert planner.swept_mirrors() == ["m001"]
