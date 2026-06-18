from __future__ import annotations

"""Phase D 黄金样本对账。

把 `build_hkg1_example_state()` 经过完整模板渲染管线（dn42_templates →
RenderedFile）得到的结果，与仓库里登记的 `examples/rendered-hkg1/` 黄金样本
做逐字节比对。任何模板逻辑、上下文构造或 desired-state 结构的非预期改动都
会让这个测试红灯，从而强制先重新生成 examples 再合并。
"""

from pathlib import Path

import pytest

from dn42_runtime import RenderedFile
from dn42_schemas.testing import build_hkg1_example_state
from dn42_templates import render_desired_state


REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = REPO_ROOT / "examples" / "rendered-hkg1"
# 黄金样本目录里只有这些文件是模板产出，其它文件（README.md 之类）忽略。
IGNORED_FILES = {"README.md"}


def _collect_golden_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for path in GOLDEN_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(GOLDEN_DIR).as_posix()
        if rel in IGNORED_FILES:
            continue
        files[rel] = path.read_text(encoding="utf-8")
    return files


def _render_to_dict() -> dict[str, str]:
    rendered: list[RenderedFile] = render_desired_state(build_hkg1_example_state())
    return {item.path: item.content for item in rendered}


@pytest.fixture(scope="module")
def golden_files() -> dict[str, str]:
    return _collect_golden_files()


@pytest.fixture(scope="module")
def rendered_files() -> dict[str, str]:
    return _render_to_dict()


def test_rendered_file_set_matches_golden(
    golden_files: dict[str, str], rendered_files: dict[str, str]
) -> None:
    rendered_only = sorted(set(rendered_files) - set(golden_files))
    golden_only = sorted(set(golden_files) - set(rendered_files))
    assert not rendered_only and not golden_only, (
        "rendered/golden file sets diverge.\n"
        f"  only in rendered: {rendered_only}\n"
        f"  only in golden:   {golden_only}\n"
        "Re-run the snippet from examples/rendered-hkg1/README.md to refresh."
    )


@pytest.mark.parametrize(
    "relative_path",
    sorted(_collect_golden_files()),
)
def test_rendered_file_matches_golden(
    relative_path: str,
    golden_files: dict[str, str],
    rendered_files: dict[str, str],
) -> None:
    expected = golden_files[relative_path]
    actual = rendered_files.get(relative_path)
    assert actual is not None, f"{relative_path} missing from rendered output"
    assert actual == expected, (
        f"rendered content for {relative_path} drifted from golden sample.\n"
        "Re-run the snippet in examples/rendered-hkg1/README.md to refresh, "
        "or revert the offending template/desired-state change."
    )
