from __future__ import annotations

"""dn42 node agent CLI 入口。

只负责参数解析、配置加载，把工作委托给 orchestrator。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# 当从源码树直接 `python apps/node-agent/agent/main.py` 启动时，
# 把基础 packages 加入 sys.path，避免在没有 `pip install -e` 的环境下导入失败。
_REPO_ROOT = Path(__file__).resolve().parents[3]
for _package in ("dn42_common", "dn42_schemas", "dn42_templates", "dn42_runtime"):
    _candidate = _REPO_ROOT / "packages" / _package
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from .core.config import AgentConfig, load_agent_config
from .core.logging import configure_logging, get_logger
from .doctor import run_doctor
from .orchestrator import run_once
from .watch import run_watch

_LOGGER = get_logger("main")


async def _run_watch_until_signal(config: AgentConfig) -> None:
    """运行常驻监听循环，收到 SIGTERM / SIGINT 即设置 stop_event 优雅退出。

    优雅退出让 ``run_watch`` 跑完当前 reconcile、不丢最后一次门铃，并经
    ``Adapters.close()`` 释放 HTTP 连接池 / Docker client，而不是被粗暴打断在
    半收敛状态（隧道 / BIRD 配置应用到一半）。

    `loop.add_signal_handler` 在类 Unix 上可用（真实节点都是 Linux）；Windows 等
    不支持的平台退化为依赖 ``asyncio.run`` 对 Ctrl+C 抛 ``KeyboardInterrupt``。
    """

    import signal

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, AttributeError, ValueError):
            # 平台不支持注册该信号：忽略，退回 KeyboardInterrupt 路径。
            pass
    await run_watch(config, stop_event=stop)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "DN42 node agent —— 默认作为后台常驻守护进程运行："
            "启动即 reconcile，并连接控制面 WebSocket 通道，收到事件就再次 reconcile。"
        )
    )
    parser.add_argument("--config", type=Path, help="agent.toml 配置文件路径")
    parser.add_argument("--controller-url", help="Control Server 基础 URL")
    parser.add_argument("--enrollment-token", help="一次性 enrollment token")
    parser.add_argument("--requested-node-id", help="希望绑定的 node_id")
    parser.add_argument("--hostname", help="覆盖 inventory 中的 hostname")
    parser.add_argument("--state-dir", type=Path, help="本地状态目录")
    parser.add_argument("--rendered-dir", type=Path, help="渲染输出目录（覆盖默认）")
    parser.add_argument("--desired-state", type=Path, help="离线运行使用的 desired-state JSON")
    parser.add_argument(
        "--mode",
        choices=["apply", "write-rendered", "plan-only"],
        help=(
            "reconcile 深度：apply（默认，写盘 + 部署 + 收敛）、"
            "write-rendered（只写渲染文件，不碰容器）、plan-only（只规划）"
        ),
    )
    # ---- 诊断用的单次模式（不再是常规运行方式，仅供手动排障）----
    diag = parser.add_mutually_exclusive_group()
    diag.add_argument(
        "--once",
        action="store_true",
        help="诊断：只跑一次 reconcile 后退出，不进入常驻循环",
    )
    diag.add_argument(
        "--plan-only",
        action="store_true",
        help="诊断：等价于 --once --mode plan-only",
    )
    diag.add_argument(
        "--doctor",
        action="store_true",
        help="诊断：跑一次自检（配置 / 状态目录 / 身份 / 控制面 / Docker / 指标）后退出",
    )
    parser.add_argument("--log-level", help="日志级别，例如 INFO / DEBUG")
    return parser


def _config_from_args(args: argparse.Namespace) -> AgentConfig:
    config = load_agent_config(args.config) if args.config else load_agent_config(None)

    overrides: dict[str, Any] = {}
    if args.controller_url is not None:
        overrides["controller_url"] = args.controller_url
    if args.enrollment_token is not None:
        overrides["enrollment_token"] = args.enrollment_token
    if args.requested_node_id is not None:
        overrides["requested_node_id"] = args.requested_node_id
    if args.hostname is not None:
        overrides["hostname"] = args.hostname
    if args.state_dir is not None:
        overrides["state_dir"] = args.state_dir
    if args.rendered_dir is not None:
        overrides["rendered_dir"] = args.rendered_dir
    if args.desired_state is not None:
        overrides["desired_state_path"] = args.desired_state

    if args.plan_only:
        if args.mode is not None and args.mode != "plan-only":
            raise SystemExit("--plan-only 与 --mode 冲突，请只用其一")
        overrides["mode"] = "plan-only"
    elif args.mode is not None:
        overrides["mode"] = args.mode
    if args.log_level is not None:
        overrides["log_level"] = args.log_level

    if (
        overrides.get("controller_url", config.controller_url) is not None
        and overrides.get("desired_state_path", config.desired_state_path) is not None
    ):
        raise SystemExit("--controller-url 与 --desired-state 互斥")

    return config.with_overrides(**overrides)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    config = _config_from_args(args)
    configure_logging(config.log_level)

    # ---- 诊断：自检后退出（不改任何状态）----
    if args.doctor:
        report = run_doctor(config)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.ok else 1

    # ---- 诊断单次模式：跑一次后退出 ----
    if args.once or args.plan_only:
        result = run_once(config)
        print(json.dumps(result.summary(), indent=2, sort_keys=True))
        if result.deploy_result is not None and not result.deploy_result.succeeded:
            return 1
        return 0

    # ---- 默认：后台常驻守护进程 ----
    if config.controller_url is None:
        raise SystemExit(
            "常驻模式需要 --controller-url（或在 agent.toml / 环境变量中配置）。"
            "如需离线单次排障，请使用 --once 或 --plan-only。"
        )
    if config.mode == "plan-only":
        raise SystemExit("plan-only 仅用于单次诊断，常驻模式请用 apply 或 write-rendered")
    try:
        asyncio.run(_run_watch_until_signal(config))
    except KeyboardInterrupt:
        # 信号处理器不可用的平台（如 Windows）的兜底优雅退出路径。
        _LOGGER.info("收到中断信号，停止守护进程")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
