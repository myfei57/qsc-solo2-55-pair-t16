"""工业过程顺序与联锁控制服务。

只对外暴露装配入口 ``Platform`` 与工厂 ``build_plant``。各组件包
（heliostat、field、receiver、salt、steam、temp、wash、alarm、ns、store、
audit、sequence、console）由 ``Platform`` 的真实调用链串联：组件之间不互相
导入对方的私有实现，只用对方暴露的状态查询与动作接口。
"""

from __future__ import annotations

__version__ = "1.0.0"

from .app import Platform, build_plant

__all__ = ["Platform", "build_plant", "__version__"]
