from __future__ import annotations

"""runtime 文件写入路径的安全性测试。

agent 会以 ``RenderedFile`` 为输入、以某个受控 ``base_dir`` 为输出根。
任何“逃逸 base_dir” 都是严重的安全问题。本文件覆盖：

* 构造阶段拒绝的路径形式：绝对 POSIX 路径、Windows 盘符 / 反斜杠、
  ``..`` 回退、嵌入在 中间的 ``..``、空串、含 NUL 字符。
* ``RenderedFile.path`` / ``content`` 必须是字符串、领头的 ``./`` 会被归一化。
* ``write_rendered_files`` 原子写入（先写到临时名再原子重命名）
  且不遗留临时文件；在被“后门”改损路径后，写盘阶段仍可识别
  “escapes target dir” 错误，不会写着在目标之外。
* ``build_file_plan`` 只报 create / noop / update；仅在
  ``prune=True`` 时诠报 delete，且仅覆盖“渲染集接管的目录”或所
  提供的 ``managed_prefixes``，避免误删 base_dir 中的其他子树。
* ``apply_rendered_files`` 提供 ``dry_run`` 不落盘、“prune 后递归清理空
  目录”、返回 outcome 包含 create / delete 汇总等语义。
"""

from pathlib import Path

import pytest

from dn42_runtime import RenderedFile, build_file_plan, write_rendered_files
from dn42_runtime import apply_rendered_files


class TestRenderedFilePathValidation:
    @pytest.mark.parametrize(
        "path",
        [
            "docker-compose.yml",
            "scripts/wg/start.sh",
            "a/b/c.txt",
            "./compose.yml",
        ],
    )
    def test_accepts_relative_paths(self, path: str) -> None:
        rf = RenderedFile(path, "x")
        # ``./compose.yml`` should normalize to ``compose.yml``.
        assert not rf.path.startswith("./")
        assert ".." not in rf.path.split("/")

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "\\Windows\\System32\\drivers\\etc\\hosts",
            "C:/Windows/cmd.exe",
            "c:\\foo\\bar",
            "../escape.yml",
            "a/../../escape.yml",
            "scripts/../../../etc/shadow",
            "",
            "with\x00nul",
        ],
    )
    def test_rejects_unsafe_paths(self, path: str) -> None:
        with pytest.raises((ValueError, TypeError)):
            RenderedFile(path, "x")

    def test_rejects_non_string_path(self) -> None:
        with pytest.raises(TypeError):
            RenderedFile(123, "x")  # type: ignore[arg-type]

    def test_rejects_non_string_content(self) -> None:
        with pytest.raises(TypeError):
            RenderedFile("a.txt", b"bytes")  # type: ignore[arg-type]


class TestWriteRenderedFiles:
    def test_writes_files_atomically(self, tmp_path: Path) -> None:
        files = [
            RenderedFile("docker-compose.yml", "version: '3'\n"),
            RenderedFile("scripts/foo.sh", "#!/bin/sh\n"),
        ]
        write_rendered_files(files, tmp_path)
        assert (tmp_path / "docker-compose.yml").read_text() == "version: '3'\n"
        assert (tmp_path / "scripts" / "foo.sh").read_text() == "#!/bin/sh\n"
        # 没有遗留临时文件
        leftovers = [
            p
            for p in tmp_path.rglob(".*tmp*")
            if p.is_file()
        ]
        assert leftovers == []

    def test_overwrites_existing_atomically(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("old")
        write_rendered_files([RenderedFile("a.txt", "new")], tmp_path)
        assert target.read_text() == "new"

    def test_resolve_keeps_files_inside_base(self, tmp_path: Path) -> None:
        # 即便构造时绕过校验，写盘时 resolve 应锁住 base。
        rendered = RenderedFile("legit.txt", "ok")
        # 通过 object.__setattr__ 强行植入坏路径模拟攻击。
        object.__setattr__(rendered, "path", "../escape.txt")
        with pytest.raises(ValueError, match="escapes target dir"):
            write_rendered_files([rendered], tmp_path)


class TestBuildFilePlanSafety:
    def test_plan_with_unsafe_path_after_construction(self, tmp_path: Path) -> None:
        rendered = RenderedFile("ok.txt", "x")
        object.__setattr__(rendered, "path", "../escape.txt")
        with pytest.raises(ValueError, match="escapes target dir"):
            build_file_plan([rendered], tmp_path)

    def test_plan_create_when_missing(self, tmp_path: Path) -> None:
        plan = build_file_plan([RenderedFile("a.txt", "x")], tmp_path)
        assert plan.summary.create == 1
        assert plan.summary.update == 0
        assert plan.summary.noop == 0

    def test_plan_noop_when_identical(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        plan = build_file_plan([RenderedFile("a.txt", "x")], tmp_path)
        assert plan.summary.noop == 1
        assert plan.summary.create == 0


class TestBuildFilePlanPrune:
    def test_no_delete_without_prune(self, tmp_path: Path) -> None:
        (tmp_path / "bird").mkdir()
        (tmp_path / "bird" / "orphan.conf").write_text("stale", encoding="utf-8")
        plan = build_file_plan([RenderedFile("bird/bird.conf", "x")], tmp_path)
        assert plan.summary.delete == 0

    def test_prune_flags_orphans_in_managed_scope(self, tmp_path: Path) -> None:
        bird_dir = tmp_path / "bird"
        bird_dir.mkdir()
        (bird_dir / "bird.conf").write_text("x", encoding="utf-8")
        (bird_dir / "orphan.conf").write_text("stale", encoding="utf-8")
        plan = build_file_plan([RenderedFile("bird/bird.conf", "x")], tmp_path, prune=True)
        delete_actions = [a for a in plan.actions if a.action == "delete"]
        assert [a.path for a in delete_actions] == ["bird/orphan.conf"]
        assert plan.summary.delete == 1
        assert plan.summary.noop == 1

    def test_prune_ignores_files_outside_managed_scope(self, tmp_path: Path) -> None:
        # 渲染集只覆盖 bird/，目标目录里的 other/ 不应被纳入受管范围。
        bird_dir = tmp_path / "bird"
        bird_dir.mkdir()
        (bird_dir / "bird.conf").write_text("x", encoding="utf-8")
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        (other_dir / "keep.txt").write_text("keep", encoding="utf-8")
        plan = build_file_plan([RenderedFile("bird/bird.conf", "x")], tmp_path, prune=True)
        assert plan.summary.delete == 0

    def test_prune_respects_explicit_managed_prefixes(self, tmp_path: Path) -> None:
        (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
        managed = tmp_path / "scripts"
        managed.mkdir()
        (managed / "old.sh").write_text("old", encoding="utf-8")
        plan = build_file_plan(
            [RenderedFile("scripts/new.sh", "new")],
            tmp_path,
            prune=True,
            managed_prefixes=["scripts"],
        )
        deletes = {a.path for a in plan.actions if a.action == "delete"}
        assert deletes == {"scripts/old.sh"}


class TestApplyRenderedFiles:
    def test_apply_creates_and_reports(self, tmp_path: Path) -> None:
        outcome = apply_rendered_files(
            [RenderedFile("a.txt", "x"), RenderedFile("b/c.txt", "y")], tmp_path
        )
        assert (tmp_path / "a.txt").read_text() == "x"
        assert (tmp_path / "b" / "c.txt").read_text() == "y"
        assert outcome.summary.create == 2
        assert outcome.errors == []
        assert {f.action for f in outcome.applied} == {"create"}

    def test_apply_dry_run_does_not_touch_disk(self, tmp_path: Path) -> None:
        outcome = apply_rendered_files(
            [RenderedFile("a.txt", "x")], tmp_path, dry_run=True
        )
        assert not (tmp_path / "a.txt").exists()
        assert outcome.dry_run is True
        assert outcome.summary.create == 1

    def test_apply_prune_deletes_orphans_and_cleans_dirs(self, tmp_path: Path) -> None:
        bird_dir = tmp_path / "bird"
        bird_dir.mkdir()
        (bird_dir / "bird.conf").write_text("x", encoding="utf-8")
        stale_dir = bird_dir / "stale"
        stale_dir.mkdir()
        (stale_dir / "old.conf").write_text("old", encoding="utf-8")

        outcome = apply_rendered_files(
            [RenderedFile("bird/bird.conf", "x")], tmp_path, prune=True
        )
        assert outcome.summary.delete == 1
        assert not (stale_dir / "old.conf").exists()
        # 因删除而变空的受管子目录应被清理。
        assert not stale_dir.exists()
        # 仍有内容的目录保留。
        assert (bird_dir / "bird.conf").exists()

