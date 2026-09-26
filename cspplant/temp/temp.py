"""温度与降额报告。

巡检只关心几件事：现在多少度、离降额还差多少、离上限还差多少、是不是已经在
降额里。:func:`build_report` 把这些口子固定下来，控制台与命令行不再各自拼装。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ReceiverLimits


@dataclass(frozen=True)
class TemperatureReport:
    """一份温度与降额报告。"""

    temperature_c: float
    target_c: float
    derate_c: float
    max_c: float
    derated: bool
    margin_to_derate_c: float
    margin_to_max_c: float

    @property
    def at_target(self) -> bool:
        return abs(self.temperature_c - self.target_c) <= max(1.0, self.target_c * 0.03)

    def as_dict(self) -> dict:
        return {
            "temperature_c": round(self.temperature_c, 2),
            "target_c": round(self.target_c, 2),
            "derate_c": round(self.derate_c, 2),
            "max_c": round(self.max_c, 2),
            "derated": self.derated,
            "margin_to_derate_c": round(self.margin_to_derate_c, 2),
            "margin_to_max_c": round(self.margin_to_max_c, 2),
            "at_target": self.at_target,
        }


def build_report(
    limits: ReceiverLimits,
    temperature_c: float,
    derated: bool,
) -> TemperatureReport:
    """按当前出口温度与降额状态生成报告。"""

    temperature = float(temperature_c)
    return TemperatureReport(
        temperature_c=temperature,
        target_c=limits.temp_target_c,
        derate_c=limits.temp_derate_c,
        max_c=limits.temp_max_c,
        derated=bool(derated),
        margin_to_derate_c=limits.temp_derate_c - temperature,
        margin_to_max_c=limits.temp_max_c - temperature,
    )
