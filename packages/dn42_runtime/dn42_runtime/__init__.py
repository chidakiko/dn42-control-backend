from __future__ import annotations

"""dn42_runtime 的公开运行时渲染与写盘 API。"""

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from dn42_schemas import PlanSummary

from .docker import (
    create_config_docker_environment,
    render_router_dockerfile,
)
from .paths import config_docker_template_dir
from .types import RenderedFile


@dataclass(frozen=True, slots=True)
class PlanAction:
    """单个渲染文件相对于目标目录的计划动作。"""

    action: str
    path: str
    desired_sha256: str | None = None
    observed_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class FilePlan:
    """整批渲染文件的写盘计划摘要与动作列表。"""

    summary: PlanSummary
    actions: list[PlanAction]


@dataclass(frozen=True, slots=True)
class AppliedFile:
    """一次 apply 中对单个文件实际执行的结果。"""

    action: str
    path: str
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ApplyOutcome:
    """`apply_rendered_files` 的结构化结果，可直接喂给 `ApplyResult`。"""

    summary: PlanSummary
    applied: list[AppliedFile]
    errors: list[str]
    dry_run: bool = False


def build_file_plan(
    rendered_files: list[RenderedFile],
    rendered_dir: Path | None = None,
    *,
    prune: bool = False,
    managed_prefixes: list[str] | None = None,
) -> FilePlan:
    """比较渲染结果与已有目录，生成 create/update/noop（可选 delete）计划。

    当 `prune=True` 且 `rendered_dir` 存在时，会额外扫描目标目录里属于"受管
    范围"但本次渲染未产出的文件，并把它们列为 `delete` 动作。受管范围由
    `managed_prefixes` 指定（相对路径前缀，按 `/` 分段匹配）；若为 `None`，则
    从本次渲染文件的顶层路径段自动推导，避免误删渲染目录以外的用户文件。
    """

    base = _resolve_base(rendered_dir) if rendered_dir is not None else None
    actions: list[PlanAction] = []
    desired_paths: set[str] = set()
    for rendered in sorted(rendered_files, key=lambda item: item.path):
        desired_paths.add(rendered.path)
        desired_hash = _hash_text(rendered.content)
        existing_path = _resolve_target(base, rendered.path) if base is not None else None
        if existing_path is None or not existing_path.exists():
            actions.append(PlanAction("create", rendered.path, desired_sha256=desired_hash))
            continue

        observed = existing_path.read_text(encoding="utf-8")
        observed_hash = _hash_text(observed)
        if observed_hash == desired_hash:
            actions.append(
                PlanAction(
                    "noop",
                    rendered.path,
                    desired_sha256=desired_hash,
                    observed_sha256=observed_hash,
                )
            )
        else:
            actions.append(
                PlanAction(
                    "update",
                    rendered.path,
                    desired_sha256=desired_hash,
                    observed_sha256=observed_hash,
                )
            )

    if prune and base is not None:
        prefixes = managed_prefixes if managed_prefixes is not None else _managed_prefixes_from(desired_paths)
        for orphan, observed_hash in _scan_orphans(base, desired_paths, prefixes):
            actions.append(
                PlanAction("delete", orphan, observed_sha256=observed_hash)
            )

    actions.sort(key=lambda item: (item.path, item.action))
    counts = {"create": 0, "update": 0, "delete": 0, "noop": 0}
    for action in actions:
        counts[action.action] += 1
    return FilePlan(summary=PlanSummary(**counts), actions=actions)


def execute_file_plan(
    plan: FilePlan,
    rendered_files: list[RenderedFile],
    rendered_dir: Path,
    *,
    dry_run: bool = False,
) -> ApplyOutcome:
    """**严格按给定计划**执行写盘/删除，绝不自行重新决策。

    计划与执行分离是"Plan 一等公民"的根基：调用方先用 `build_file_plan`
    产出权威计划（可用于上报、审计、plan-only 展示），再把**同一份**计划
    交给本函数执行——保证"计划说什么、执行做什么、上报报什么"三者一致。

    `dry_run=True` 时只回放计划、不触盘。`errors` 收集每个文件的
    `OSError`，调用方可直接放进 `ApplyResult.errors`。
    """

    base = _resolve_base(rendered_dir)
    content_by_path = {rendered.path: rendered.content for rendered in rendered_files}
    applied: list[AppliedFile] = []
    errors: list[str] = []

    for action in plan.actions:
        if action.action == "noop":
            applied.append(AppliedFile("noop", action.path, action.desired_sha256))
            continue
        if dry_run:
            applied.append(
                AppliedFile(action.action, action.path, action.desired_sha256)
            )
            continue
        try:
            if action.action in ("create", "update"):
                content = content_by_path.get(action.path)
                if content is None:
                    errors.append(
                        f"{action.action} {action.path}: rendered content missing for planned action"
                    )
                    continue
                _atomic_write(base, action.path, content)
                applied.append(
                    AppliedFile(action.action, action.path, action.desired_sha256)
                )
            elif action.action == "delete":
                _delete_target(base, action.path)
                applied.append(AppliedFile("delete", action.path, action.observed_sha256))
        except OSError as exc:
            errors.append(f"{action.action} {action.path}: {exc}")

    return ApplyOutcome(
        summary=plan.summary, applied=applied, errors=errors, dry_run=dry_run
    )


def apply_rendered_files(
    rendered_files: list[RenderedFile],
    rendered_dir: Path,
    *,
    prune: bool = False,
    managed_prefixes: list[str] | None = None,
    dry_run: bool = False,
) -> ApplyOutcome:
    """便捷入口：`build_file_plan` + `execute_file_plan` 一步完成。

    需要把计划用于上报/审计的调用方应分两步调用，确保全程只有一份计划。
    """

    plan = build_file_plan(
        rendered_files, rendered_dir, prune=prune, managed_prefixes=managed_prefixes
    )
    return execute_file_plan(plan, rendered_files, rendered_dir, dry_run=dry_run)


def write_rendered_files(rendered_files: list[RenderedFile], rendered_dir: Path) -> None:
    """把渲染结果原子写入目标目录，并自动创建缺失父目录。

    每个文件先写到同目录下的 `.<name>.tmp.<pid>` 临时文件，再 `os.replace`
    到最终位置，以保证读者要么看到旧版本要么看到新版本。
    """

    base = _resolve_base(rendered_dir)
    for rendered in rendered_files:
        _atomic_write(base, rendered.path, rendered.content)


def _atomic_write(base: Path, relative: str, content: str) -> None:
    """把单个文件原子写入 `base/relative`（先写临时文件再 `os.replace`）。"""

    path = _resolve_target(base, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        tmp_path.write_text(content, encoding="utf-8", newline="\n")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _delete_target(base: Path, relative: str) -> None:
    """删除 `base/relative` 文件，并清理因此变空的受管父目录（不越过 base）。"""

    path = _resolve_target(base, relative)
    if path.exists():
        path.unlink()
    parent = path.parent
    while parent != base and parent.is_dir():
        try:
            next(parent.iterdir())
            break
        except StopIteration:
            parent.rmdir()
            parent = parent.parent


def _managed_prefixes_from(desired_paths: set[str]) -> list[str]:
    """从期望文件路径推导受管顶层范围（每个路径取第一段）。"""

    return sorted({path.split("/", 1)[0] for path in desired_paths})


def _within_managed(relative: str, prefixes: list[str]) -> bool:
    """`relative` 是否落在任一受管前缀之内（按 `/` 分段匹配）。"""

    rel_parts = relative.split("/")
    for prefix in prefixes:
        prefix_parts = prefix.strip("/").split("/")
        if rel_parts[: len(prefix_parts)] == prefix_parts:
            return True
    return False


def _scan_orphans(
    base: Path, desired_paths: set[str], prefixes: list[str]
) -> list[tuple[str, str]]:
    """扫描受管范围内、本次渲染未产出的孤儿文件，返回 (相对路径, sha256)。"""

    orphans: list[tuple[str, str]] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if name.startswith(".") and ".tmp." in name:
            continue  # 跳过原子写入残留的临时文件
        relative = path.relative_to(base).as_posix()
        if relative in desired_paths:
            continue
        if not _within_managed(relative, prefixes):
            continue
        orphans.append((relative, _hash_text(path.read_text(encoding="utf-8"))))
    return sorted(orphans)


def _resolve_base(rendered_dir: Path) -> Path:
    return Path(rendered_dir).resolve(strict=False)


def _resolve_target(base: Path, relative: str) -> Path:
    """把 `RenderedFile.path` 拼到 `base` 下，并强制 resolve 后仍在 `base` 内。

    `RenderedFile` 已经过路径校验，这里再做一次 ``relative_to`` 兜底，应对软
    链接 / 大小写折叠等极端情况。
    """

    candidate = (base / relative).resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(
            f"rendered file path {relative!r} escapes target dir {base!s}"
        ) from exc
    return candidate


def _hash_text(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


__all__ = [
    "AppliedFile",
    "ApplyOutcome",
    "FilePlan",
    "PlanAction",
    "RenderedFile",
    "apply_rendered_files",
    "build_file_plan",
    "execute_file_plan",
    "config_docker_template_dir",
    "create_config_docker_environment",
    "render_router_dockerfile",
    "write_rendered_files",
]
