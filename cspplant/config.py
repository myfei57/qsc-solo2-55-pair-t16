"""平台配置。

配置来自 JSON 文件（标准库解析），覆盖定日镜对焦公差与转速上限、辐照采样
周期与过期时间、吸热器温度带与流量确认时间、熔盐罐液位边界与泵升速斜率、
蒸汽压力带与甩负荷提前量、清洗车喷淋时长与回水流量、闩锁复位延迟与告警
升级窗口，以及状态存储目录。任何缺失或越界取值都在装配阶段抛出
:class:`ConfigError`，不允许带着半份配置启动控制链。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import ConfigError

_MISSING = object()


def _section(mapping: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = mapping.get(key)
    if value is None:
        raise ConfigError(f"配置缺少段落 {key!r}", component="config")
    if not isinstance(value, Mapping):
        raise ConfigError(f"配置段落 {key!r} 必须是映射", component="config")
    return value


def _number(section: Mapping[str, Any], key: str, *, minimum: float | None = None) -> float:
    value = section.get(key, _MISSING)
    if value is _MISSING:
        raise ConfigError(f"配置缺少字段 {key!r}", component="config")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"配置字段 {key!r} 必须是数值", component="config")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ConfigError(f"配置字段 {key!r} 不得小于 {minimum}", component="config")
    return number


def _integer(section: Mapping[str, Any], key: str, *, minimum: int = 1) -> int:
    number = _number(section, key, minimum=float(minimum))
    if abs(number - round(number)) > 1e-9:
        raise ConfigError(f"配置字段 {key!r} 必须是整数", component="config")
    return int(round(number))


@dataclass(frozen=True)
class HeliostatLimits:
    """定日镜对焦精度与转速约束。"""

    aim_tolerance_deg: float
    azimuth_rate_deg_s: float
    elevation_rate_deg_s: float
    stow_elevation_deg: float
    mirror_area_m2: float
    reflectivity: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "HeliostatLimits":
        limits = cls(
            aim_tolerance_deg=_number(section, "aim_tolerance_deg", minimum=0.0),
            azimuth_rate_deg_s=_number(section, "azimuth_rate_deg_s", minimum=0.01),
            elevation_rate_deg_s=_number(section, "elevation_rate_deg_s", minimum=0.01),
            stow_elevation_deg=_number(section, "stow_elevation_deg", minimum=0.0),
            mirror_area_m2=_number(section, "mirror_area_m2", minimum=1.0),
            reflectivity=_number(section, "reflectivity", minimum=0.0),
        )
        if not 0.0 < limits.reflectivity <= 1.0:
            raise ConfigError("镜面反射率必须落在 (0, 1] 区间", component="config")
        if limits.stow_elevation_deg >= 90.0:
            raise ConfigError("收起仰角必须小于 90 度", component="config")
        return limits

    def within_tolerance(self, error_deg: float) -> bool:
        """方位／仰角误差是否落在对焦公差内。"""

        return abs(error_deg) <= self.aim_tolerance_deg


@dataclass(frozen=True)
class IrradianceLimits:
    """辐照采样与有效期约束。"""

    dni_min_w_m2: float
    dni_max_w_m2: float
    sample_period_s: float
    stale_after_s: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "IrradianceLimits":
        limits = cls(
            dni_min_w_m2=_number(section, "dni_min_w_m2", minimum=0.0),
            dni_max_w_m2=_number(section, "dni_max_w_m2", minimum=1.0),
            sample_period_s=_number(section, "sample_period_s", minimum=1.0),
            stale_after_s=_number(section, "stale_after_s", minimum=1.0),
        )
        if limits.dni_min_w_m2 >= limits.dni_max_w_m2:
            raise ConfigError("辐照下限必须小于上限", component="config")
        if limits.stale_after_s < limits.sample_period_s:
            raise ConfigError("辐照过期时间不得小于采样周期", component="config")
        return limits

    def in_range(self, dni_w_m2: float) -> bool:
        """采样值是否落在可信区间内。"""

        return self.dni_min_w_m2 <= float(dni_w_m2) <= self.dni_max_w_m2


@dataclass(frozen=True)
class FieldLimits:
    """镜场调度约束。"""

    focus_threshold_w_m2: float
    dispatch_rate_pct_s: float
    sweep_batch: int
    safe_zone_margin_m: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "FieldLimits":
        return cls(
            focus_threshold_w_m2=_number(section, "focus_threshold_w_m2", minimum=0.0),
            dispatch_rate_pct_s=_number(section, "dispatch_rate_pct_s", minimum=0.1),
            sweep_batch=_integer(section, "sweep_batch", minimum=1),
            safe_zone_margin_m=_number(section, "safe_zone_margin_m", minimum=0.0),
        )


@dataclass(frozen=True)
class ReceiverLimits:
    """吸热器温度带、流量门控与阀门速度约束。"""

    temp_target_c: float
    temp_derate_c: float
    temp_max_c: float
    flow_min_kg_s: float
    flow_confirm_s: float
    valve_travel_pct_s: float
    ambient_c: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "ReceiverLimits":
        limits = cls(
            temp_target_c=_number(section, "temp_target_c", minimum=1.0),
            temp_derate_c=_number(section, "temp_derate_c", minimum=1.0),
            temp_max_c=_number(section, "temp_max_c", minimum=1.0),
            flow_min_kg_s=_number(section, "flow_min_kg_s", minimum=0.1),
            flow_confirm_s=_number(section, "flow_confirm_s", minimum=0.1),
            valve_travel_pct_s=_number(section, "valve_travel_pct_s", minimum=0.1),
            ambient_c=_number(section, "ambient_c"),
        )
        if not limits.temp_target_c < limits.temp_derate_c < limits.temp_max_c:
            raise ConfigError("吸热器温度带必须满足 目标 < 降额 < 上限", component="config")
        return limits

    def confirmed(self, temperature_c: float) -> bool:
        """出口温度是否落在目标带内（允许 ±3% 目标值）。"""

        band = max(1.0, self.temp_target_c * 0.03)
        return abs(float(temperature_c) - self.temp_target_c) <= band

    def derate_band(self, temperature_c: float) -> bool:
        """出口温度是否已经进入降额区间。"""

        return self.temp_derate_c <= float(temperature_c) < self.temp_max_c

    def overtemperature(self, temperature_c: float) -> bool:
        """出口温度是否越限。"""

        return float(temperature_c) >= self.temp_max_c

    def flow_sufficient(self, flow_kg_s: float) -> bool:
        """流量是否达到低流量联锁门限。"""

        return float(flow_kg_s) >= self.flow_min_kg_s


@dataclass(frozen=True)
class SaltLimits:
    """熔盐液位、泵升速与母管压力约束。"""

    cold_level_min_pct: float
    hot_level_max_pct: float
    pump_ramp_kg_s2: float
    handoff_confirm_s: float
    flow_baseline_kg_s: float
    flow_tolerance_kg_s: float
    header_pressure_target_mpa: float
    header_pressure_max_mpa: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "SaltLimits":
        limits = cls(
            cold_level_min_pct=_number(section, "cold_level_min_pct", minimum=0.0),
            hot_level_max_pct=_number(section, "hot_level_max_pct", minimum=1.0),
            pump_ramp_kg_s2=_number(section, "pump_ramp_kg_s2", minimum=0.01),
            handoff_confirm_s=_number(section, "handoff_confirm_s", minimum=0.1),
            flow_baseline_kg_s=_number(section, "flow_baseline_kg_s", minimum=1.0),
            flow_tolerance_kg_s=_number(section, "flow_tolerance_kg_s", minimum=0.1),
            header_pressure_target_mpa=_number(section, "header_pressure_target_mpa", minimum=0.1),
            header_pressure_max_mpa=_number(section, "header_pressure_max_mpa", minimum=0.2),
        )
        if limits.hot_level_max_pct > 100.0:
            raise ConfigError("热盐罐上限不得超过 100%", component="config")
        if limits.header_pressure_max_mpa <= limits.header_pressure_target_mpa:
            raise ConfigError("母管压力上限必须大于目标值", component="config")
        return limits

    def low_flow_threshold(self, baseline_kg_s: float) -> float:
        """按标定基线给出的低流量联锁门限。"""

        return float(baseline_kg_s) - self.flow_tolerance_kg_s

    def header_overpressure(self, pressure_mpa: float) -> bool:
        """母管是否超压。"""

        return float(pressure_mpa) >= self.header_pressure_max_mpa


@dataclass(frozen=True)
class SteamLimits:
    """蒸汽压力带、旁路提前量与汽机负荷约束。"""

    pressure_target_mpa: float
    pressure_band_mpa: float
    bypass_lead_s: float
    load_max_mw: float
    load_rate_mw_s: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "SteamLimits":
        return cls(
            pressure_target_mpa=_number(section, "pressure_target_mpa", minimum=0.1),
            pressure_band_mpa=_number(section, "pressure_band_mpa", minimum=0.01),
            bypass_lead_s=_number(section, "bypass_lead_s", minimum=0.0),
            load_max_mw=_number(section, "load_max_mw", minimum=1.0),
            load_rate_mw_s=_number(section, "load_rate_mw_s", minimum=0.01),
        )

    def settled(self, pressure_mpa: float) -> bool:
        """蒸汽压力是否进入目标带。"""

        return abs(float(pressure_mpa) - self.pressure_target_mpa) <= self.pressure_band_mpa


@dataclass(frozen=True)
class WashLimits:
    """镜面清洗与回水约束。"""

    wash_duration_s: float
    return_flow_m3_h: float
    drain_lead_s: float
    truck_capacity_m3: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "WashLimits":
        limits = cls(
            wash_duration_s=_number(section, "wash_duration_s", minimum=1.0),
            return_flow_m3_h=_number(section, "return_flow_m3_h", minimum=0.1),
            drain_lead_s=_number(section, "drain_lead_s", minimum=0.0),
            truck_capacity_m3=_number(section, "truck_capacity_m3", minimum=0.1),
        )
        if limits.drain_lead_s >= limits.wash_duration_s:
            raise ConfigError("排空提前量必须小于喷淋时长", component="config")
        return limits


@dataclass(frozen=True)
class AlarmLimits:
    """故障闩锁与升级窗口约束。"""

    latch_reset_delay_s: float
    escalation_window_s: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "AlarmLimits":
        limits = cls(
            latch_reset_delay_s=_number(section, "latch_reset_delay_s", minimum=0.0),
            escalation_window_s=_number(section, "escalation_window_s", minimum=1.0),
        )
        if limits.escalation_window_s <= limits.latch_reset_delay_s:
            raise ConfigError("升级窗口必须大于闩锁复位延迟", component="config")
        return limits


@dataclass(frozen=True)
class PlatformConfig:
    """一份完整的平台配置。"""

    name: str
    storage_root: str
    heliostat: HeliostatLimits
    irradiance: IrradianceLimits
    field: FieldLimits
    receiver: ReceiverLimits
    salt: SaltLimits
    steam: SteamLimits
    wash: WashLimits
    alarm: AlarmLimits

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "PlatformConfig":
        meta = _section(mapping, "platform")
        name = meta.get("name")
        storage_root = meta.get("storage_root")
        if not isinstance(name, str) or not name:
            raise ConfigError("platform.name 必须是非空字符串", component="config")
        if not isinstance(storage_root, str) or not storage_root:
            raise ConfigError("platform.storage_root 必须是非空字符串", component="config")

        limits = _section(mapping, "limits")
        return cls(
            name=name,
            storage_root=storage_root,
            heliostat=HeliostatLimits.from_mapping(_section(limits, "heliostat")),
            irradiance=IrradianceLimits.from_mapping(_section(limits, "irradiance")),
            field=FieldLimits.from_mapping(_section(limits, "field")),
            receiver=ReceiverLimits.from_mapping(_section(limits, "receiver")),
            salt=SaltLimits.from_mapping(_section(limits, "salt")),
            steam=SteamLimits.from_mapping(_section(limits, "steam")),
            wash=WashLimits.from_mapping(_section(limits, "wash")),
            alarm=AlarmLimits.from_mapping(_section(limits, "alarm")),
        )


def load_config(path: str) -> PlatformConfig:
    """从 JSON 文件读取配置。"""

    if not os.path.isfile(path):
        raise ConfigError(f"配置文件不存在: {path}", component="config")
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, Mapping):
        raise ConfigError("配置文件顶层必须是映射", component="config")
    return PlatformConfig.from_mapping(raw)


def default_config() -> PlatformConfig:
    """内置默认配置，供未指定配置文件时装配使用。"""

    return PlatformConfig(
        name="field-control",
        storage_root="var/state",
        heliostat=HeliostatLimits(0.6, 1.2, 0.9, 12.0, 450.0, 0.94),
        irradiance=IrradianceLimits(250.0, 1100.0, 30.0, 180.0),
        field=FieldLimits(320.0, 6.0, 12, 6.0),
        receiver=ReceiverLimits(600.0, 620.0, 720.0, 42.0, 20.0, 8.0, 22.0),
        salt=SaltLimits(18.0, 96.0, 1.4, 15.0, 58.0, 9.0, 2.5, 3.4),
        steam=SteamLimits(12.0, 0.8, 6.0, 30.0, 1.0),
        wash=WashLimits(90.0, 26.0, 4.0, 9.0),
        alarm=AlarmLimits(5.0, 120.0),
    )


def config_to_mapping(cfg: PlatformConfig) -> dict:
    """把配置还原成可打印的映射。"""

    return {
        "platform": {"name": cfg.name, "storage_root": cfg.storage_root},
        "limits": {
            "heliostat": {
                "aim_tolerance_deg": cfg.heliostat.aim_tolerance_deg,
                "azimuth_rate_deg_s": cfg.heliostat.azimuth_rate_deg_s,
                "elevation_rate_deg_s": cfg.heliostat.elevation_rate_deg_s,
                "stow_elevation_deg": cfg.heliostat.stow_elevation_deg,
                "mirror_area_m2": cfg.heliostat.mirror_area_m2,
                "reflectivity": cfg.heliostat.reflectivity,
            },
            "irradiance": {
                "dni_min_w_m2": cfg.irradiance.dni_min_w_m2,
                "dni_max_w_m2": cfg.irradiance.dni_max_w_m2,
                "sample_period_s": cfg.irradiance.sample_period_s,
                "stale_after_s": cfg.irradiance.stale_after_s,
            },
            "field": {
                "focus_threshold_w_m2": cfg.field.focus_threshold_w_m2,
                "dispatch_rate_pct_s": cfg.field.dispatch_rate_pct_s,
                "sweep_batch": cfg.field.sweep_batch,
                "safe_zone_margin_m": cfg.field.safe_zone_margin_m,
            },
            "receiver": {
                "temp_target_c": cfg.receiver.temp_target_c,
                "temp_derate_c": cfg.receiver.temp_derate_c,
                "temp_max_c": cfg.receiver.temp_max_c,
                "flow_min_kg_s": cfg.receiver.flow_min_kg_s,
                "flow_confirm_s": cfg.receiver.flow_confirm_s,
                "valve_travel_pct_s": cfg.receiver.valve_travel_pct_s,
                "ambient_c": cfg.receiver.ambient_c,
            },
            "salt": {
                "cold_level_min_pct": cfg.salt.cold_level_min_pct,
                "hot_level_max_pct": cfg.salt.hot_level_max_pct,
                "pump_ramp_kg_s2": cfg.salt.pump_ramp_kg_s2,
                "handoff_confirm_s": cfg.salt.handoff_confirm_s,
                "flow_baseline_kg_s": cfg.salt.flow_baseline_kg_s,
                "flow_tolerance_kg_s": cfg.salt.flow_tolerance_kg_s,
                "header_pressure_target_mpa": cfg.salt.header_pressure_target_mpa,
                "header_pressure_max_mpa": cfg.salt.header_pressure_max_mpa,
            },
            "steam": {
                "pressure_target_mpa": cfg.steam.pressure_target_mpa,
                "pressure_band_mpa": cfg.steam.pressure_band_mpa,
                "bypass_lead_s": cfg.steam.bypass_lead_s,
                "load_max_mw": cfg.steam.load_max_mw,
                "load_rate_mw_s": cfg.steam.load_rate_mw_s,
            },
            "wash": {
                "wash_duration_s": cfg.wash.wash_duration_s,
                "return_flow_m3_h": cfg.wash.return_flow_m3_h,
                "drain_lead_s": cfg.wash.drain_lead_s,
                "truck_capacity_m3": cfg.wash.truck_capacity_m3,
            },
            "alarm": {
                "latch_reset_delay_s": cfg.alarm.latch_reset_delay_s,
                "escalation_window_s": cfg.alarm.escalation_window_s,
            },
        },
    }
