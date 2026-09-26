"""吸热器。

吸热器把入射热功率转成熔盐温升，并守住三条边界：没有确认流量不得通盐（否则
干烧）、出口温度进入降额带必须压低负荷上限、越过上限必须跳闸并闩锁。阀门、
流量门控、温度模型、降额控制器与母管接口各自独立，顺序由编排保证。
"""

from __future__ import annotations

from .derate import DerateController, DerateState
from .flow import FlowGate, FlowStatus
from .header import SaltHeader, HeaderState
from .receiver import AT_POWER, CIRCULATING, COLD, DERATED, TRIPPED, Receiver
from .sensor import TemperatureSensor
from .snapshot import ReceiverSnapshot
from .thermal import SALT_CP_KJ_KG_K, ThermalModel
from .threshold import ThresholdModel
from .valve import InletValve

__all__ = [
    "DerateController",
    "DerateState",
    "FlowGate",
    "FlowStatus",
    "SaltHeader",
    "HeaderState",
    "Receiver",
    "COLD",
    "CIRCULATING",
    "AT_POWER",
    "DERATED",
    "TRIPPED",
    "TemperatureSensor",
    "ReceiverSnapshot",
    "ThermalModel",
    "ThresholdModel",
    "InletValve",
    "SALT_CP_KJ_KG_K",
]
