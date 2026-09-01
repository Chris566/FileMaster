"""W10: core/archiver.py 单元测试.

覆盖:
  - ArchiveFormat enum (extension / from_path)
  - ArchiveTask / ArchiveResult / ArchiveEntry dataclass
  - Archiver.archive() 3 种格式
  - Archiver.archive_with_progress() 进度回调 + 取消 + 临时文件清理
  - Archiver.archive_by_category() 多分类 + 取消
  - cleanup_archive_tmps 清理 .filemaster.tmp.* 残留
  - safe_rename 协作 (W9 atomic 模式)
"""
from __future__ import annotations

import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from filemaster.core.archiver import (
    ArchiveEntry,
    ArchiveFormat,
    Archiver,
    ArchiveResult,
    ArchiveTask,
    cleanup_archive_tmps,
)

# 临时文件后缀 (W9 safe_rename 落地)
_TMP_SUFFIX = ".filemaster.tmp"


# ---------------- fixture ----------------


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    """创建含 5 个文件的临时目录."""
    d = tmp_path / "src"
    d.mkdir()
    (d / "a.txt").write_text("AAA", encoding="utf-8")
    (d / "b.txt").write_text("BBBB", encoding="utf-8")
    (d / "c.md").write_text("CCCCC", encoding="utf-8")
    (d / "d.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    (d / "e.jpg").write_bytes(b"\xff\xd8\xff" + b"y" * 200)
    return d


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "out"


# ---------------- ArchiveFormat ----------------


class TestArchiveFormat:
    def test_zip_extension(self) -> None:
        assert ArchiveFormat.ZIP.extension == ".zip"

    def test_tar_gz_extension(self) -> None:
        assert ArchiveFormat.TAR_GZ.extension == ".tar.gz"

    def test_tar_bz2_extension(self) -> None:
        assert ArchiveFormat.TAR_BZ2.extension == ".tar.bz2"

    def test_from_path_zip(self) -> None:
        assert ArchiveFormat.from_path(Path("foo.zip")) is ArchiveFormat.ZIP

    def test_from_path_tar_gz(self) -> None:
        assert ArchiveFormat.from_path(Path("foo.tar.gz")) is ArchiveFormat.TAR_GZ
        assert ArchiveFormat.from_path(Path("FOO.TAR.GZ")) is ArchiveFormat.TAR_GZ

    def test_from_path_tar_bz2(self) -> None:
        assert ArchiveFormat.from_path(Path("foo.tar.bz2")) is ArchiveFormat.TAR_BZ2

    def test_from_path_unknown_defaults_zip(self) -> None:
        assert ArchiveFormat.from_path(Path("foo.unknown")) is ArchiveFormat.ZIP


# ---------------- Dataclasses ----------------


class TestDataclasses:
    def test_archive_task_defaults(self, sample_dir: Path, output_dir: Path) -> None:
        task = ArchiveTask(
            source_files=(sample_dir / "a.txt",),
            archive_path=output_dir / "a.zip",
        )
        assert task.fmt is ArchiveFormat.ZIP
        assert task.compression == 6
        assert task.base_dir is None

    def test_archive_result_defaults(self, tmp_path: Path) -> None:
        r = ArchiveResult(tmp_path / "x.zip", 0, 0, 0.0, "OK")
        assert r.message == ""

    def test_archive_entry_default_timestamp(self, sample_dir: Path) -> None:
        entry = ArchiveEntry(
            archive_path=Path("/tmp/x.zip"),
            source_files=(sample_dir / "a.txt",),
            fmt=ArchiveFormat.ZIP,
        )
        # YYYY-MM-DDTHH:MM:SS
        assert "T" in entry.timestamp
        assert len(entry.timestamp) == 19


# ---------------- Archiver.archive() 基础 ----------------


class TestArchiveBasic:
    def test_archive_zip(self, sample_dir: Path, output_dir: Path) -> None:
        files = [sample_dir / f for f in ["a.txt", "b.txt", "c.md"]]
        task = ArchiveTask(
            source_files=tuple(files),
            archive_path=output_dir / "test.zip",
        )
        written = Archiver().archive(task)
        assert (output_dir / "test.zip").is_file()
        assert written == sum(f.stat().st_size for f in files)
        with zipfile.ZipFile(output_dir / "test.zip") as zf:
            assert sorted(zf.namelist()) == ["a.txt", "b.txt", "c.md"]

    def test_archive_tar_gz(self, sample_dir: Path, output_dir: Path) -> None:
        files = [sample_dir / "a.txt", sample_dir / "b.txt"]
        task = ArchiveTask(
            source_files=tuple(files),
            archive_path=output_dir / "test.tar.gz",
            fmt=ArchiveFormat.TAR_GZ,
        )
        Archiver().archive(task)
        assert (output_dir / "test.tar.gz").is_file()
        with tarfile.open(output_dir / "test.tar.gz", "r:gz") as tf:
            assert sorted(tf.getnames()) == ["a.txt", "b.txt"]

    def test_archive_tar_bz2(self, sample_dir: Path, output_dir: Path) -> None:
        files = [sample_dir / "a.txt"]
        task = ArchiveTask(
            source_files=tuple(files),
            archive_path=output_dir / "test.tar.bz2",
            fmt=ArchiveFormat.TAR_BZ2,
        )
        Archiver().archive(task)
        assert (output_dir / "test.tar.bz2").is_file()
        with tarfile.open(output_dir / "test.tar.bz2", "r:bz2") as tf:
            assert tf.getnames() == ["a.txt"]

    def test_archive_with_base_dir(self, sample_dir: Path, output_dir: Path) -> None:
        files = [sample_dir / "a.txt", sample_dir / "b.txt"]
        task = ArchiveTask(
            source_files=tuple(files),
            archive_path=output_dir / "test.zip",
            base_dir=sample_dir,
        )
        Archiver().archive(task)
        with zipfile.ZipFile(output_dir / "test.zip") as zf:
            # 相对 sample_dir 的路径
            assert sorted(zf.namelist()) == ["a.txt", "b.txt"]

    def test_archive_skips_missing_files(self, sample_dir: Path, output_dir: Path) -> None:
        files = [sample_dir / "a.txt", sample_dir / "missing.txt"]
        task = ArchiveTask(
            source_files=tuple(files),
            archive_path=output_dir / "test.zip",
        )
        Archiver().archive(task)
        with zipfile.ZipFile(output_dir / "test.zip") as zf:
            assert zf.namelist() == ["a.txt"]


# ---------------- Archiver.archive_with_progress() ----------------


class TestArchiveWithProgress:
    def test_ok(self, sample_dir: Path, output_dir: Path) -> None:
        files = sorted(sample_dir.iterdir())
        progress_calls: list[tuple[int, int, str, int]] = []

        def on_progress(i: int, total: int, f: Path, b: int) -> None:
            progress_calls.append((i, total, f.name, b))

        result = Archiver().archive_with_progress(
            files, output_dir / "test.zip",
            on_progress=on_progress,
        )
        assert result.status == "OK"
        assert result.source_count == 5
        assert result.written_bytes > 0
        assert result.elapsed >= 0
        assert (output_dir / "test.zip").is_file()
        # 5 次回调, 每次 (i, total, file, written)
        assert len(progress_calls) == 5
        assert progress_calls[0][0] == 1
        assert progress_calls[-1][0] == 5
        assert progress_calls[-1][1] == 5

    def test_no_files_returns_error(self, tmp_path: Path, output_dir: Path) -> None:
        result = Archiver().archive_with_progress(
            [], output_dir / "test.zip",
        )
        assert result.status == "ERROR"
        assert "无" in result.message
        assert not (output_dir / "test.zip").exists()

    def test_tar_gz_ok(self, sample_dir: Path, output_dir: Path) -> None:
        files = sorted(sample_dir.iterdir())[:3]
        result = Archiver().archive_with_progress(
            files, output_dir / "test.tar.gz", fmt=ArchiveFormat.TAR_GZ,
        )
        assert result.status == "OK"
        assert (output_dir / "test.tar.gz").is_file()

    def test_tar_bz2_ok(self, sample_dir: Path, output_dir: Path) -> None:
        files = sorted(sample_dir.iterdir())[:3]
        result = Archiver().archive_with_progress(
            files, output_dir / "test.tar.bz2", fmt=ArchiveFormat.TAR_BZ2,
        )
        assert result.status == "OK"
        assert (output_dir / "test.tar.bz2").is_file()

    def test_progress_callback_exception_doesnt_break(
        self, sample_dir: Path, output_dir: Path
    ) -> None:
        """回调抛异常被吞掉, 归档仍完成."""

        def bad_on_progress(i: int, total: int, f: Path, b: int) -> None:
            raise RuntimeError("boom")

        files = sorted(sample_dir.iterdir())
        result = Archiver().archive_with_progress(
            files, output_dir / "test.zip", on_progress=bad_on_progress,
        )
        assert result.status == "OK"
        assert (output_dir / "test.zip").is_file()

    def test_cancellation_before_start(self, sample_dir: Path, output_dir: Path) -> None:
        """is_cancelled=True 立即返回 CANCELLED."""
        files = sorted(sample_dir.iterdir())
        cancel_flag = {"v": True}
        result = Archiver().archive_with_progress(
            files, output_dir / "test.zip",
            is_cancelled=lambda: cancel_flag["v"],
        )
        assert result.status == "CANCELLED"
        # tmp 文件应被清理
        leftover = list(output_dir.glob(f"*{_TMP_SUFFIX}"))
        assert leftover == []
        # 目标文件不应存在
        assert not (output_dir / "test.zip").exists()

    def test_cancellation_midway(self, sample_dir: Path, output_dir: Path) -> None:
        """处理到第 2 个时取消."""
        files = sorted(sample_dir.iterdir())
        # 调用计数: 第 1 次 False, 第 2 次起 True
        call_count = {"n": 0}

        def is_cancelled() -> bool:
            call_count["n"] += 1
            return call_count["n"] >= 2

        result = Archiver().archive_with_progress(
            files, output_dir / "test.zip",
            is_cancelled=is_cancelled,
        )
        assert result.status == "CANCELLED"
        # 不应该有 test.zip
        assert not (output_dir / "test.zip").exists()
        # 临时文件应被清理
        leftover = list(output_dir.glob(f"*{_TMP_SUFFIX}"))
        assert leftover == []
        # 处理了 1 个 (cancel 在第 2 次文件前触发)
        assert "已处理 1/5" in result.message

    def test_cancellation_during_zip_write(
        self, sample_dir: Path, output_dir: Path
    ) -> None:
        """取消发生在 zip 写入过程中 (W9 模式: 关闭后清理)."""
        files = sorted(sample_dir.iterdir())
        # 在第 3 个文件后取消
        cancel_after = {"n": 0}

        def is_cancelled() -> bool:
            cancel_after["n"] += 1
            return cancel_after["n"] > 3

        result = Archiver().archive_with_progress(
            files, output_dir / "test.zip", is_cancelled=is_cancelled,
        )
        assert result.status == "CANCELLED"
        assert not (output_dir / "test.zip").exists()
        assert list(output_dir.glob(f"*{_TMP_SUFFIX}")) == []

    def test_atomic_rename_no_tmp_after_ok(
        self, sample_dir: Path, output_dir: Path
    ) -> None:
        """成功完成时, tmp 文件被 rename 到目标, 不留 tmp."""
        files = sorted(sample_dir.iterdir())
        Archiver().archive_with_progress(
            files, output_dir / "test.zip",
        )
        assert (output_dir / "test.zip").is_file()
        assert list(output_dir.glob(f"*{_TMP_SUFFIX}")) == []

    def test_compression_zero(self, sample_dir: Path, output_dir: Path) -> None:
        """compression=0 也跑得通 (zip: 0=STORE 不压缩)."""
        files = [sample_dir / "a.txt"]
        result = Archiver().archive_with_progress(
            files, output_dir / "test.zip", compression=0,
        )
        assert result.status == "OK"


# ---------------- Archiver.archive_by_category() ----------------


class TestArchiveByCategory:
    def test_multiple_categories(self, sample_dir: Path, output_dir: Path) -> None:
        # a.txt + b.txt + c.md (TEXT) + d.png (IMAGE) + e.jpg (IMAGE)
        files = sorted(sample_dir.iterdir())
        results = Archiver().archive_by_category(
            files, output_dir, fmt=ArchiveFormat.ZIP,
        )
        # 至少 2 类: IMAGE + TEXT (或其他)
        assert len(results) >= 2
        # 检查每个 OK 的归档存在
        for _cat, r in results.items():
            if r.status == "OK":
                assert r.archive_path.is_file()
                assert r.source_count > 0
        # 应该有 IMAGE 类
        assert "IMAGE" in results or any("IMAGE" in str(r.archive_path) for r in results.values())

    def test_empty_files(self, output_dir: Path) -> None:
        results = Archiver().archive_by_category([], output_dir)
        assert results == {}

    def test_only_other_category(self, output_dir: Path, tmp_path: Path) -> None:
        # 创建未分类扩展名文件
        d = tmp_path / "weird"
        d.mkdir()
        (d / "x.xyz").write_text("x")
        (d / "y.qqq").write_text("y")
        results = Archiver().archive_by_category(
            sorted(d.iterdir()), output_dir,
        )
        assert "OTHER" in results
        assert results["OTHER"].source_count == 2
        assert results["OTHER"].status == "OK"
        assert (output_dir / "OTHER.zip").is_file()

    def test_cancellation_between_categories(
        self, sample_dir: Path, output_dir: Path
    ) -> None:
        """分卷之间取消 — 至少有一个 CANCELLED."""
        files = sorted(sample_dir.iterdir())
        cancel_count = {"n": 0}

        def is_cancelled() -> bool:
            cancel_count["n"] += 1
            # 第 2 次检查时取消 (第 1 个分类完成时)
            return cancel_count["n"] > 1

        results = Archiver().archive_by_category(
            files, output_dir, is_cancelled=is_cancelled,
        )
        # 至少 1 个分类被取消
        assert any(r.status == "CANCELLED" for r in results.values())


# ---------------- cleanup_archive_tmps ----------------


class TestCleanupArchiveTmps:
    def test_no_tmps(self, tmp_path: Path) -> None:
        n = cleanup_archive_tmps(tmp_path)
        assert n == 0

    def test_cleans_orphan_tmps(self, tmp_path: Path) -> None:
        # 模拟崩溃残留
        (tmp_path / "backup.zip.filemaster.tmp.12345678").write_bytes(b"partial")
        (tmp_path / "another.zip.filemaster.tmp.abcdef01").write_bytes(b"x")
        (tmp_path / "real.zip").write_bytes(b"complete")
        n = cleanup_archive_tmps(tmp_path)
        assert n == 2
        # 真实文件保留
        assert (tmp_path / "real.zip").is_file()
        # tmp 已清理
        leftover = list(tmp_path.glob("*.filemaster.tmp.*"))
        assert leftover == []

    def test_cleans_recursively(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "x.zip.filemaster.tmp.deadbeef").write_bytes(b"p")
        n = cleanup_archive_tmps(tmp_path)
        assert n == 1


# ---------------- safe_rename 协作 ----------------


class TestSafeRenameIntegration:
    """W10 写入 tmp → W9 safe_rename → 目标路径."""

    def test_atomic_rename_leaves_no_tmp(
        self, sample_dir: Path, output_dir: Path
    ) -> None:
        files = [sample_dir / "a.txt"]
        result = Archiver().archive_with_progress(files, output_dir / "test.zip")
        assert result.status == "OK"
        # 没有 .filemaster.tmp.* 残留
        assert list(output_dir.glob(f"*{_TMP_SUFFIX}")) == []
        # 目标存在且有效
        assert (output_dir / "test.zip").is_file()

    def test_cancellation_rolls_back_tmp(
        self, sample_dir: Path, output_dir: Path
    ) -> None:
        """取消后 tmp 文件应被清理, 目标不应存在."""
        files = sorted(sample_dir.iterdir())

        def cancel_now() -> bool:
            return True

        result = Archiver().archive_with_progress(
            files, output_dir / "test.zip", is_cancelled=cancel_now,
        )
        assert result.status == "CANCELLED"
        # 无残留
        assert list(output_dir.glob(f"*{_TMP_SUFFIX}")) == []
        # 无目标
        assert not (output_dir / "test.zip").exists()
