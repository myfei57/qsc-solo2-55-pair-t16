"""定日镜驱动状态机。

状态只在 ``idle``、``tracking``（追日）、``defocus``（离焦）、``stowed``
（收起）、``manual``（手动接管，自动追日不得覆盖）之间切换。角度按方位与
仰角两个自由度分别限速推进，``step`` 显式驱动，控制循环与验证环境都能按
固定步长观察镜面转到哪了。
"""

from __future__ import annotations

from ..config import HeliostatLimits
from ..errors import InterlockError
from .aim import AimAngles, angle_error, normalize_azimuth, shortest_azimuth_delta, stow_angles
from .snapshot import AimSnapshot, HeliostatSnapshot

IDLE = "idle"
TRACKING = "tracking"
DEFOCUS = "defocus"
STOWED = "stowed"
MANUAL = "manual"


class HeliostatDrive:
    """单台定日镜的两轴驱动。"""

    def __init__(self, name: str, limits: HeliostatLimits, azimuth_deg: float = 180.0) -> None:
        self._name = name
        self._limits = limits
        self._azimuth = normalize_azimuth(azimuth_deg)
        self._elevation = float(limits.stow_elevation_deg)
        self._target = AimAngles(self._azimuth, self._elevation)
        self._state = IDLE
        self._manual = False
        self._excluded = False
        self._aimed = False
        self._turns = 0
        self._rate_limited = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def limits(self) -> HeliostatLimits:
        return self._limits

    @property
    def state(self) -> str:
        return self._state

    @property
    def azimuth_deg(self) -> float:
        return self._azimuth

    @property
    def elevation_deg(self) -> float:
        return self._elevation

    @property
    def manual(self) -> bool:
        return self._manual

    @property
    def excluded(self) -> bool:
        return self._excluded

    @property
    def aimed_at_receiver(self) -> bool:
        """镜面当前的目标是否指向吸热器（收起与离焦都为假）。"""

        return self._aimed

    def mark_excluded(self, excluded: bool) -> None:
        """标记本镜是否处于避让状态（检修票／清洗批次）。"""

        self._excluded = bool(excluded)

    def set_manual(self, active: bool) -> None:
        """切换手动接管标志。"""

        self._manual = bool(active)
        if active:
            self._state = MANUAL

    def aim(self, target: AimAngles, *, auto: bool = True) -> AimAngles:
        """下达追日目标；手动接管期间自动目标被拒绝。"""

        if auto and self._manual:
            raise InterlockError(f"{self._name} 处于手动接管，自动追日目标被拒绝", component="heliostat")
        self._target = AimAngles(normalize_azimuth(target.azimuth_deg), float(target.elevation_deg))
        self._state = TRACKING if auto else MANUAL
        self._aimed = auto
        return self._target

    def defocus(self) -> AimAngles:
        """离焦：镜面转到不对准吸热器的姿态。"""

        target = stow_angles(self._limits.stow_elevation_deg, self._azimuth)
        self._target = target
        self._state = DEFOCUS
        self._aimed = False
        return target

    def stow(self) -> AimAngles:
        """收起：镜面转到仰角 0 度（朝下），任何情况下都允许。"""

        target = stow_angles(0.0, self._azimuth)
        self._target = target
        self._manual = False
        self._state = STOWED
        self._aimed = False
        return target

    def step(self, dt: float) -> AimAngles:
        """按步长推进两个轴，返回推进后的实际角度。"""

        if dt <= 0:
            raise ValueError("步长必须为正")
        if self._state in (STOWED, IDLE) and abs(self._target.elevation_deg - self._elevation) < 1e-9:
            return AimAngles(self._azimuth, self._elevation)

        az_step = self._limits.azimuth_rate_deg_s * dt
        el_step = self._limits.elevation_rate_deg_s * dt
        target = self.target_angles()
        az_delta = shortest_azimuth_delta(self._azimuth, target.azimuth_deg)
        el_delta = target.elevation_deg - self._elevation

        if abs(az_delta) > az_step:
            self._azimuth = normalize_azimuth(self._azimuth + (az_step if az_delta > 0 else -az_step))
            self._rate_limited += 1
        else:
            self._azimuth = normalize_azimuth(target.azimuth_deg)
        if abs(el_delta) > el_step:
            self._elevation += el_step if el_delta > 0 else -el_step
            self._rate_limited += 1
        else:
            self._elevation = target.elevation_deg

        if self.aligned():
            self._turns += 1
            if self._state in (TRACKING, DEFOCUS, STOWED):
                self._state = IDLE
        return AimAngles(self._azimuth, self._elevation)

    def aligned(self) -> bool:
        """两个轴是否都已经到位。"""

        error = angle_error(self.current_angles(), self._target)
        return self._limits.within_tolerance(error.azimuth_deg) and self._limits.within_tolerance(
            error.elevation_deg
        )

    def at_target(self) -> bool:
        """两个轴是否已经精确停在目标角度（收起与离焦用精确判定）。"""

        error = angle_error(self.current_angles(), self._target)
        return abs(error.azimuth_deg) < 1e-9 and abs(error.elevation_deg) < 1e-9

    def current_angles(self) -> AimAngles:
        """返回当前角度。"""

        return AimAngles(self._azimuth, self._elevation)

    def target_angles(self) -> AimAngles:
        """返回目标角度。"""

        return self._target

    def turns(self) -> int:
        """返回累计到位次数。"""

        return self._turns

    def rate_limited_steps(self) -> int:
        """返回累计被限速的轴步进次数。"""

        return self._rate_limited

    def aim_snapshot(self) -> AimSnapshot:
        """返回对焦精度快照。"""

        error = angle_error(self.current_angles(), self._target)
        return AimSnapshot(
            azimuth_deg=self._azimuth,
            elevation_deg=self._elevation,
            azimuth_error_deg=error.azimuth_deg,
            elevation_error_deg=error.elevation_deg,
            within_tolerance=self._limits.within_tolerance(error.azimuth_deg)
            and self._limits.within_tolerance(error.elevation_deg),
        )

    def snapshot(self) -> HeliostatSnapshot:
        """返回驱动状态快照。"""

        return HeliostatSnapshot(
            name=self._name,
            state=self._state,
            manual=self._manual,
            excluded=self._excluded,
            azimuth_deg=self._azimuth,
            elevation_deg=self._elevation,
            target_azimuth_deg=self._target.azimuth_deg,
            target_elevation_deg=self._target.elevation_deg,
            turns=self._turns,
        )
