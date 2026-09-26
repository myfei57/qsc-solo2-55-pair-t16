"""出口温度传感器。

热偶读数带噪声，直接拿单点做保护会误跳。传感器做一阶滤波：新值按固定系数
并入历史值，既能跟上真实温升，又不会被单点尖峰带跑。保护判定用的始终是滤波
后的读数，原始值单独留档供巡检核对。
"""

from __future__ import annotations


class TemperatureSensor:
    """带一阶滤波的出口温度传感器。"""

    def __init__(self, ambient_c: float, alpha: float = 0.35) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("滤波系数必须落在 (0, 1]")
        self._value = float(ambient_c)
        self._raw = float(ambient_c)
        self._alpha = float(alpha)
        self._samples = 0
        self._peak = float(ambient_c)

    @property
    def alpha(self) -> float:
        return self._alpha

    def read(self, raw_c: float) -> float:
        """读入一次原始温度，返回滤波后的值。"""

        self._raw = float(raw_c)
        self._value += self._alpha * (self._raw - self._value)
        self._samples += 1
        self._peak = max(self._peak, self._value)
        return self._value

    def reset(self, value_c: float) -> float:
        """把传感器复位到给定温度。"""

        self._value = float(value_c)
        self._raw = float(value_c)
        self._peak = float(value_c)
        return self._value

    def value_c(self) -> float:
        """返回滤波后的温度。"""

        return self._value

    def raw_c(self) -> float:
        """返回最近一次原始读数。"""

        return self._raw

    def peak_c(self) -> float:
        """返回滤波读数的历史最高值。"""

        return self._peak

    def samples(self) -> int:
        """返回累计采样次数。"""

        return self._samples
