from __future__ import annotations

"""dn42_templates 命令行入口（render / apply）的单元测试。"""

import json
from pathlib import Path

import pytest

from dn42_schemas.testing import build_hkg1_example_state
from dn42_templates.__main__ import main


@pytest.fixture()
def state_file(tmp_path: Path) -> Path:
    state = build_hkg1_example_state()
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state.model_dump(mode="json")), encoding="utf-8")
    return path


def test_render_writes_files(tmp_path: Path, state_file: Path, capsys: pytest.CaptureFixture) -> None:
    out = tmp_path / "rendered"

    rc = main(["render", "--state", str(state_file), "--out", str(out)])

    assert rc == 0
    assert (out / "bird" / "bird.conf").exists()
    captured = capsys.readouterr()
    assert "rendered" in captured.out


def test_apply_dry_run_does_not_write(tmp_path: Path, state_file: Path, capsys: pytest.CaptureFixture) -> None:
    out = tmp_path / "rendered"

    rc = main(["apply", "--state", str(state_file), "--out", str(out), "--dry-run"])

    assert rc == 0
    assert not out.exists()  # dry-run 不落盘
    captured = capsys.readouterr()
    assert "dry-run plan" in captured.out


def test_apply_writes_then_prune_removes_orphan(
    tmp_path: Path, state_file: Path
) -> None:
    out = tmp_path / "rendered"

    # 首次 apply 落盘。
    assert main(["apply", "--state", str(state_file), "--out", str(out)]) == 0
    assert (out / "bird" / "bird.conf").exists()

    # 在受管前缀下放一个孤儿文件，再带 --prune apply。
    orphan = out / "bird" / "orphan.conf"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("stale", encoding="utf-8")

    assert main(["apply", "--state", str(state_file), "--out", str(out), "--prune"]) == 0
    assert not orphan.exists()


def test_missing_command_exits_with_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code != 0
