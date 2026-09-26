"""命令行入口。

五个子命令覆盖日常使用：

``serve``   启动控制台 HTTP 服务，可选地在启动后探测接口；
``demo``    在本地跑一遍完整流程（落盘追日 → 熔盐循环 → 带负荷 → 控制周期 →
            并发探测 → 手动接管 → 清洗 → 冷热罐切换 → 停堆与复位），用于交付
            前的真实运行检查；
``status``  打印当前状态快照；
``config``  打印生效配置；
``reset``   清掉落盘的平台计数。
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .app import Platform, build_plant
from .clock import ManualClock, SystemClock
from .config import config_to_mapping, default_config, load_config
from .console.server import ConsoleServer, probe_endpoints
from .errors import PlantError

DEFAULT_PROBE_PATHS = ("/api/status", "/api/readiness", "/api/topology", "/api/audit", "/receiver")


def _build(args: argparse.Namespace) -> Platform:
    config = load_config(args.config) if getattr(args, "config", None) else default_config()
    if getattr(args, "storage_root", None):
        return build_plant(config, storage_root=args.storage_root)
    return Platform(config=config)


def _dump(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _cmd_serve(args: argparse.Namespace) -> int:
    platform = _build(args)
    server = ConsoleServer(platform, host=args.host, port=args.port)
    base_url = server.start()
    payload: dict = {"listening": base_url, "routes": server.api.routes()}
    if args.probe:
        payload["probe"] = probe_endpoints(base_url, list(DEFAULT_PROBE_PATHS))
    _dump(payload)
    if args.duration > 0:
        try:
            time.sleep(args.duration)
        except KeyboardInterrupt:  # pragma: no cover - 交互路径
            pass
        server.stop()
        return 0
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:  # pragma: no cover - 交互路径
        server.stop()
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    clock = ManualClock() if args.fast else SystemClock()
    config_path = getattr(args, "config", None)
    config = load_config(config_path) if config_path else default_config()
    platform = build_plant(config, clock=clock, storage_root=getattr(args, "storage_root", None))

    # 演示从干净基线起步：先清掉上一轮落盘的平台计数。
    platform.reset_state()

    startup = platform.start()
    launch = platform.start_plant(args.dni, args.load_mw, args.minute)
    dispatch = platform.dispatch(100.0, "demo")
    cycles = [platform.cycle(dt=args.dt) for _ in range(args.cycles)]
    forecast = platform.forecast(args.minute, horizon_min=30.0)
    concurrency = platform.concurrency_probe(rounds=args.rounds)
    manual = platform.manual_aim(args.mirror, 195.0, 32.0, "demo-operator")
    tracking_after_manual = platform.track(args.dni, args.minute)
    released = platform.release_manual(args.mirror)
    washing = platform.wash(["m021", "m022", "m023"], "maint-north")
    handoff = platform.changeover()
    disturbance = platform.disturb_steam(0.6)

    trip = platform.trip("overtemperature")
    if args.fast:
        clock.advance(config.alarm.latch_reset_delay_s + 1.0)
    reset = platform.reset_trip("demo-operator")

    # 演示自检用临时端口，避免和本机常驻服务抢 8080。
    server = ConsoleServer(platform, port=0)
    base_url = server.start()
    probe = probe_endpoints(base_url, list(DEFAULT_PROBE_PATHS))
    server.stop()

    report = {
        "startup": startup,
        "launch": {
            key: launch[key] for key in ("ok", "stage", "load_mw", "flow_kg_s", "violations")
        },
        "dispatch": dispatch,
        "cycles_run": len(cycles),
        "last_cycle": cycles[-1],
        "forecast": {
            "minutes_to_derate": forecast["minutes_to_derate"],
            "points": forecast["points"][:3],
        },
        "concurrency": concurrency,
        "manual": {"mirror": manual["mirror"], "released": released["released"]},
        "tracking_after_manual": {
            "ok": tracking_after_manual["ok"],
            "violations": tracking_after_manual["violations"],
        },
        "washing": {key: washing[key] for key in ("ok", "washed", "mirrors", "violations")},
        "handoff": {key: handoff[key] for key in ("ok", "peak_pressure_mpa", "max_pumps_running", "violations")},
        "disturbance": disturbance,
        "trip": {key: trip[key] for key in ("ok", "latched", "violations")},
        "reset": {key: reset[key] for key in ("ok", "state", "violations")},
        "probe": probe,
        "status": platform.status(),
    }
    _dump(report)

    failures = [name for name, item in probe.items() if not item["ok"]]
    for name, operation in (
        ("launch", launch),
        ("tracking_after_manual", tracking_after_manual),
        ("washing", washing),
        ("handoff", handoff),
        ("trip", trip),
        ("reset", reset),
    ):
        if not operation.get("ok"):
            failures.extend(f"{name}:{item}" for item in operation.get("violations", []))
    if failures:
        print(f"检查未通过: {failures}", file=sys.stderr)
        return 1
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    platform = _build(args)
    payload = platform.status()
    payload["topology"] = platform.topology.summary()
    payload["namespaces"] = platform.namespaces.describe()
    payload["zones"] = platform.zones.describe()
    payload["trend"] = platform.trend.snapshot()
    _dump(payload)
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    config_path = getattr(args, "config", None)
    config = load_config(config_path) if config_path else default_config()
    _dump(config_to_mapping(config))
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    platform = _build(args)
    removed = platform.reset_state()
    _dump({"storage_root": platform.store.root, "platform_state_removed": removed})
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="JSON 配置文件路径", default=argparse.SUPPRESS)
    common.add_argument("--storage-root", help="覆盖状态存储目录", default=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(
        prog="cspplant", description="工业过程顺序与联锁控制服务", parents=[common]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动控制台服务", parents=[common])
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--duration", type=float, default=0.0, help="运行多少秒后退出，0 表示常驻")
    serve.add_argument("--probe", action="store_true", help="启动后探测接口")
    serve.set_defaults(func=_cmd_serve)

    demo = sub.add_parser("demo", help="跑一遍完整流程", parents=[common])
    demo.add_argument("--dni", type=float, default=850.0, help="演示用的 DNI (W/m²)")
    demo.add_argument("--load-mw", type=float, default=30.0, help="启动时的汽机负荷 (MW)")
    demo.add_argument("--minute", type=float, default=720.0, help="演示用的当日分钟数")
    demo.add_argument("--cycles", type=int, default=420, help="演示控制周期数")
    demo.add_argument("--dt", type=float, default=1.0, help="控制周期步长（秒）")
    demo.add_argument("--rounds", type=int, default=24, help="并发探测轮数")
    demo.add_argument("--mirror", default="m021", help="手动接管的镜子")
    demo.add_argument("--fast", action="store_true", help="使用可推进时钟，跳过真实等待")
    demo.set_defaults(func=_cmd_demo)

    status = sub.add_parser("status", help="打印状态快照", parents=[common])
    status.set_defaults(func=_cmd_status)

    config = sub.add_parser("config", help="打印生效配置", parents=[common])
    config.set_defaults(func=_cmd_config)

    reset = sub.add_parser("reset", help="清掉落盘的平台计数", parents=[common])
    reset.set_defaults(func=_cmd_reset)
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行主入口。"""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except PlantError as error:
        print(json.dumps(error.as_record(), ensure_ascii=False), file=sys.stderr)
        return 2
