from __future__ import annotations

"""`python -m dn42_templates` 命令行入口。

提供两个子命令，便于运维与 Agent 旁路调试，不依赖控制面：

* ``render`` —— 从 DesiredState 文件渲染整套部署文件到输出目录。
* ``apply``  —— 渲染后用 ``dn42_runtime.apply_rendered_files`` 落盘，支持 ``--prune``
  与 ``--dry-run``，并打印 create/update/delete 概要。

示例::

    python -m dn42_templates render --state state.yaml --out ./rendered
    python -m dn42_templates apply --state state.yaml --out ./rendered --prune --dry-run
"""

import argparse
import sys
from pathlib import Path

from dn42_runtime import apply_rendered_files, write_rendered_files
from dn42_schemas import load_desired_state

from .desired_state import render_desired_state


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state",
        required=True,
        type=Path,
        help="DesiredState 文件路径（.json / .yaml / .yml）",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="渲染输出目录",
    )


def _cmd_render(args: argparse.Namespace) -> int:
    state = load_desired_state(args.state)
    files = render_desired_state(state)
    write_rendered_files(files, args.out)
    print(f"rendered {len(files)} file(s) to {args.out}")
    for rendered in files:
        print(f"  {rendered.path}")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    state = load_desired_state(args.state)
    files = render_desired_state(state)
    outcome = apply_rendered_files(
        files,
        args.out,
        prune=args.prune,
        dry_run=args.dry_run,
    )
    summary = outcome.summary
    label = "dry-run plan" if outcome.dry_run else "applied"
    print(
        f"{label}: create={summary.create} update={summary.update} "
        f"delete={summary.delete} noop={summary.noop}"
    )
    for applied in outcome.applied:
        print(f"  {applied.action:<7} {applied.path}")
    for error in outcome.errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if outcome.errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dn42_templates")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render", help="渲染 DesiredState 到输出目录")
    _add_common_args(render_parser)
    render_parser.set_defaults(func=_cmd_render)

    apply_parser = subparsers.add_parser("apply", help="渲染并落盘（支持 prune / dry-run）")
    _add_common_args(apply_parser)
    apply_parser.add_argument(
        "--prune",
        action="store_true",
        help="删除受管范围内本次未渲染的孤儿文件",
    )
    apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只计算并打印计划，不实际写盘",
    )
    apply_parser.set_defaults(func=_cmd_apply)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
