"""W10 follow-up: undo dispatcher + UndoStack.restore_latest 测试.

覆盖:
  - RestoreEntryResult dataclass
  - restore_entry: Archive / CopyOnly / Classify / Delete / Rename* / 未实现
  - Archive: 删 target / 跳过不存在 / dry-run / OSError
  - Classify: 反向 move / overwrite 冲突 / dry-run
  - Delete: 不可恢复
  - UndoStack.restore_latest: 弹一批 + 还原 / 空栈
  - Worker 集成: ArchiveWorker 跑完 → restore_latest 还原归档
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from filemaster.core.archiver import ArchiveFormat, Archiver
from filemaster.core.undo import (
    OperationType,
    RestoreEntryResult,
    UndoEntry,
    UndoStack,
    restore_entry,
)

# ---------------- RestoreEntryResult ----------------


class TestRestoreEntryResult:
    def test_defaults(self, tmp_path: Path) -> None:
        r = RestoreEntryResult(target=tmp_path / "x.zip", operation="Archive", success=True)
        assert r.skipped is False
        assert r.error is None
        assert r.message == ""


# ---------------- Archive dispatcher ----------------


class TestArchiveRestore:
    def test_deletes_archive(self, tmp_path: Path) -> None:
        archive = tmp_path / "backup.zip"
        archive.write_bytes(b"PK\x03\x04")
        entry = UndoEntry(operation="Archive", target=archive)

        result = restore_entry(entry)
        assert result.success is True
        assert result.skipped is False
        assert not archive.exists()
        assert "已删除" in result.message

    def test_skips_when_missing(self, tmp_path: Path) -> None:
        archive = tmp_path / "missing.zip"
        entry = UndoEntry(operation="Archive", target=archive)
        result = restore_entry(entry)
        assert result.success is True
        assert result.skipped is True
        assert "不存在" in result.message

    def test_dry_run_keeps_file(self, tmp_path: Path) -> None:
        archive = tmp_path / "test.zip"
        archive.write_bytes(b"PK")
        entry = UndoEntry(operation="Archive", target=archive)
        result = restore_entry(entry, dry_run=True)
        assert result.success is True
        assert result.skipped is False
        assert archive.exists()  # dry-run 不真删
        assert "DRY-RUN" in result.message

    def test_target_none_fails(self) -> None:
        entry = UndoEntry(operation="Archive", target=None)
        result = restore_entry(entry)
        assert result.success is False
        assert "target" in result.error

    def test_delete_real_archive_with_content(self, tmp_path: Path) -> None:
        """真创建一个 zip, 还原后内容真没了."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("A")
        (src_dir / "b.txt").write_text("B")
        archive = tmp_path / "out.zip"

        # 实际归档
        Archiver().archive_with_progress(
            [src_dir / "a.txt", src_dir / "b.txt"], archive,
        )
        assert archive.is_file()
        with zipfile.ZipFile(archive) as zf:
            assert sorted(zf.namelist()) == ["a.txt", "b.txt"]

        # 还原
        entry = UndoEntry(operation="Archive", target=archive)
        result = restore_entry(entry)
        assert result.success is True
        assert not archive.exists()
        # 源文件还在 (Archive 不动源)
        assert (src_dir / "a.txt").is_file()
        assert (src_dir / "b.txt").is_file()


# ---------------- CopyOnly dispatcher ----------------


class TestCopyOnlyRestore:
    def test_deletes_copy(self, tmp_path: Path) -> None:
        copy = tmp_path / "copy.txt"
        copy.write_text("copy")
        original = tmp_path / "original.txt"
        original.write_text("original")
        entry = UndoEntry(operation="CopyOnly", source=original, target=copy)

        result = restore_entry(entry)
        assert result.success is True
        assert not copy.exists()
        assert original.exists()  # 源未动


# ---------------- Classify dispatcher ----------------


class TestClassifyRestore:
    def test_moves_back(self, tmp_path: Path) -> None:
        original = tmp_path / "doc.pdf"
        categorized = tmp_path / "PDF" / "doc.pdf"
        categorized.parent.mkdir()
        categorized.write_text("doc")
        entry = UndoEntry(
            operation="Classify", source=original, target=categorized,
        )

        result = restore_entry(entry)
        assert result.success is True
        assert not categorized.exists()
        assert original.exists()

    def test_overwrite_protection(self, tmp_path: Path) -> None:
        original = tmp_path / "doc.pdf"
        original.write_text("ORIGINAL")
        categorized = tmp_path / "PDF" / "doc.pdf"
        categorized.parent.mkdir()
        categorized.write_text("MOVED")
        entry = UndoEntry(
            operation="Classify", source=original, target=categorized,
        )
        result = restore_entry(entry)  # 不 overwrite
        assert result.success is True
        assert result.skipped is True
        # 源仍在, 目标也仍在
        assert original.exists()
        assert categorized.exists()

    def test_overwrite_true_forces(self, tmp_path: Path) -> None:
        original = tmp_path / "doc.pdf"
        original.write_text("ORIGINAL")
        categorized = tmp_path / "PDF" / "doc.pdf"
        categorized.parent.mkdir()
        categorized.write_text("MOVED")
        entry = UndoEntry(
            operation="Classify", source=original, target=categorized,
        )
        result = restore_entry(entry, overwrite=True)
        assert result.success is True
        assert result.skipped is False
        # categorized 已移回 original 位置
        assert not categorized.exists()
        assert original.exists()


# ---------------- Delete (不可恢复) ----------------


class TestDeleteReject:
    def test_delete_rejected(self, tmp_path: Path) -> None:
        entry = UndoEntry(
            operation="Delete", target=tmp_path / "x.txt",
        )
        result = restore_entry(entry)
        assert result.success is False
        assert "不可恢复" in result.error


# ---------------- Rename* dispatcher ----------------


class TestRenameRestore:
    def test_restore_from_backup(self, tmp_path: Path) -> None:
        backup = tmp_path / "backup.bak"
        backup.write_text("ORIGINAL")
        target = tmp_path / "renamed.txt"
        entry = UndoEntry(
            operation="RenameAndOverwrite", target=target, backup_path=backup,
        )
        result = restore_entry(entry)
        assert result.success is True
        assert not backup.exists()  # 移走
        assert target.read_text() == "ORIGINAL"

    def test_no_backup_fails(self) -> None:
        entry = UndoEntry(
            operation="RenameAndOverwrite", target=Path("/tmp/x"), backup_path=None,
        )
        result = restore_entry(entry)
        assert result.success is False
        assert "backup_path" in result.error

    def test_missing_backup_fails(self, tmp_path: Path) -> None:
        entry = UndoEntry(
            operation="RenameAndOverwrite",
            target=tmp_path / "x",
            backup_path=tmp_path / "lost.bak",
        )
        result = restore_entry(entry)
        assert result.success is False
        assert "丢失" in result.error


# ---------------- 未实现的 operation ----------------


class TestUnknownOperation:
    def test_returns_error(self, tmp_path: Path) -> None:
        # 用一个不在 Literal 里的字符串 (cast 绕过)
        entry = UndoEntry(operation="UnknownFutureOp", target=tmp_path / "x")  # type: ignore[arg-type]
        result = restore_entry(entry)
        assert result.success is False
        assert "未实现" in result.error


# ---------------- UndoStack.restore_latest ----------------


class TestUndoStackRestoreLatest:
    def test_empty_stack(self) -> None:
        undo = UndoStack()
        assert undo.restore_latest() == []
        assert len(undo) == 0

    def test_pops_and_restores_archive(self, tmp_path: Path) -> None:
        undo = UndoStack()
        archive = tmp_path / "backup.zip"
        archive.write_bytes(b"PK")
        undo.push([UndoEntry(operation="Archive", target=archive)])

        assert len(undo) == 1
        results = undo.restore_latest()
        assert len(results) == 1
        assert results[0].success is True
        assert not archive.exists()
        assert len(undo) == 0  # 已 pop

    def test_batch_step_restores_all(self, tmp_path: Path) -> None:
        """一批多个 entry 全部还原."""
        undo = UndoStack()
        a1 = tmp_path / "a1.zip"
        a2 = tmp_path / "a2.zip"
        a1.write_bytes(b"PK")
        a2.write_bytes(b"PK")
        undo.push([
            UndoEntry(operation="Archive", target=a1),
            UndoEntry(operation="Archive", target=a2),
        ])
        results = undo.restore_latest()
        assert len(results) == 2
        assert all(r.success for r in results)
        assert not a1.exists()
        assert not a2.exists()

    def test_dry_run_does_not_pop_in_pop_only(
        self, tmp_path: Path
    ) -> None:
        """dry_run=False (默认) 时也仍 pop, 因为恢复就是 pop 出来的."""
        undo = UndoStack()
        archive = tmp_path / "x.zip"
        archive.write_bytes(b"PK")
        undo.push([UndoEntry(operation="Archive", target=archive)])

        undo.restore_latest(dry_run=True)
        # dry-run 不删文件
        assert archive.exists()
        # 但 pop 仍发生 (栈空)
        assert len(undo) == 0

    def test_lifo_order(self, tmp_path: Path) -> None:
        """LIFO: restore_latest 弹最近 push 的 step."""
        undo = UndoStack()
        a1 = tmp_path / "step1.zip"
        a2 = tmp_path / "step2.zip"
        a1.write_bytes(b"PK")
        a2.write_bytes(b"PK")
        undo.push([UndoEntry(operation="Archive", target=a1)])
        undo.push([UndoEntry(operation="Archive", target=a2)])

        # 弹 step2 先
        results = undo.restore_latest()
        assert results[0].target == a2
        assert not a2.exists()
        assert a1.exists()

        # 再弹 step1
        results = undo.restore_latest()
        assert results[0].target == a1
        assert not a1.exists()


# ---------------- Worker 集成测试 ----------------


class TestArchiveWorkerUndoIntegration:
    """ArchiveWorker.run 完成后, UndoStack 里应有 step;
    调 restore_latest 应把归档文件删掉 (源不动)."""

    def test_worker_then_restore_deletes_archive(
        self, tmp_path: Path
    ) -> None:
        from filemaster.workers.archiver import ArchiveWorker

        # 准备源文件
        src = tmp_path / "src"
        src.mkdir()
        files = []
        for i in range(3):
            f = src / f"f{i}.txt"
            f.write_text(f"file {i}")
            files.append(f)

        # 准备 output
        out = tmp_path / "out"
        out.mkdir()

        # 跑 worker
        undo = UndoStack()
        worker = ArchiveWorker(
            files, out, archive_name="backup",
            fmt=ArchiveFormat.ZIP, undo_stack=undo,
        )
        worker.run()

        # 验证归档存在, 源还在, undo 栈有 1 step
        archive = out / "backup.zip"
        assert archive.is_file()
        assert all(f.is_file() for f in files)
        assert len(undo) == 1

        # 调 restore_latest 还原
        results = undo.restore_latest()
        assert len(results) == 1
        assert results[0].success is True
        assert not archive.exists()  # 归档删了
        # 源文件未动
        assert all(f.is_file() for f in files)
        # 栈空
        assert len(undo) == 0

    def test_by_category_worker_then_restore(
        self, tmp_path: Path
    ) -> None:
        """按 category 模式: 多个归档全部还原."""
        from filemaster.workers.archiver import ArchiveWorker

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("text")
        (src / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        out = tmp_path / "out"
        out.mkdir()

        undo = UndoStack()
        worker = ArchiveWorker(
            sorted(src.iterdir()), out,
            fmt=ArchiveFormat.ZIP, by_category=True,
            undo_stack=undo,
        )
        worker.run()

        # 至少有 IMAGE 和 TEXT/OTHER 两个归档
        archives = list(out.glob("*.zip"))
        assert len(archives) >= 2
        assert len(undo) == 1

        # 还原
        results = undo.restore_latest()
        assert all(r.success for r in results)
        # 全部归档都删了
        remaining = list(out.glob("*.zip"))
        assert remaining == []
        # 源文件还在
        assert (src / "a.txt").is_file()
        assert (src / "b.png").is_file()
