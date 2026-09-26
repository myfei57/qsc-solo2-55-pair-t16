"""控制台 HTTP 服务。

四个页面给值班员看，一组 JSON 接口给图形化客户端用：镜场页看辐照、对焦与
调度，吸热器页看温度带与降额，熔盐页看罐位与联锁门限，告警页看闩锁与升级。
服务用标准库 ``http.server`` 承载，不引入额外依赖。
"""

from __future__ import annotations

from .api import Api
from .pages import PAGE_PATHS, render_page
from .router import Route, Router
from .server import ConsoleServer, probe_endpoints

__all__ = [
    "Api",
    "PAGE_PATHS",
    "render_page",
    "Route",
    "Router",
    "ConsoleServer",
    "probe_endpoints",
]
