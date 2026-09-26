"""故障闩锁与告警。

告警是"现在越限了"，闩锁是"越限之后必须有人处理过才准复位"。两者分开：规则
负责判定，闩锁负责留住状态，升级策略按重复次数与严重度决定要不要叫人。任何
情况下都不允许"温度回落就自动复位"。
"""

from __future__ import annotations

from .escalation import Escalation, EscalationPolicy
from .history import AlarmEntry, AlarmHistory
from .latch import FaultLatch, LatchState
from .rules import ALARM, WARNING, AlarmEvent, AlarmRule, AlarmRules, default_rules

__all__ = [
    "Escalation",
    "EscalationPolicy",
    "AlarmEntry",
    "AlarmHistory",
    "FaultLatch",
    "LatchState",
    "AlarmEvent",
    "AlarmRule",
    "AlarmRules",
    "default_rules",
    "ALARM",
    "WARNING",
]
