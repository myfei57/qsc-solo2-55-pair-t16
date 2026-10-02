"""启动断点续做与有序回退。

- 每步进度落检查点（``recovery/startup``），重启后按阶段判定续做或回退；
- 判定粒度定死为**阶段**，阶段内步骤只做幂等跳过，不做步骤级作废；
- 迁移阶段（参数核对）恢复时一律重做，核对不过按相反顺序退干净；
- 恢复过程全部落审计，控制台经 ``/api/recovery`` 与 ``/recovery`` 页查看。
"""

from .checkpoint import CheckpointStore, RunCheckpoint
from .params import diff_snapshots, fingerprint, parameter_snapshot, run_params
from .plan import RecoveryPlan, build_plan
from .service import PHASE_ORDER, RecoveryService

__all__ = [
    "CheckpointStore",
    "PHASE_ORDER",
    "RecoveryPlan",
    "RecoveryService",
    "RunCheckpoint",
    "build_plan",
    "diff_snapshots",
    "fingerprint",
    "parameter_snapshot",
    "run_params",
]
