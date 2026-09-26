"""测试夹具：所有用例都从干净存储和可推进时钟起步。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cspplant.app import Platform, build_plant  # noqa: E402
from cspplant.clock import ManualClock  # noqa: E402
from cspplant.config import default_config  # noqa: E402


@pytest.fixture()
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture()
def platform(tmp_path, clock) -> Platform:
    return build_plant(default_config(), clock=clock, storage_root=str(tmp_path / "state"))


@pytest.fixture()
def online(platform) -> Platform:
    """已追日、已建立熔盐循环、已带负荷的平台。"""

    platform.start()
    platform.track(800.0, 720.0)
    platform.circulate()
    platform.dispatch(100.0, "fixture")
    return platform
