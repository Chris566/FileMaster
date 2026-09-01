"""W10: workers/archiver.py 单元测试.

覆盖:
  - ArchiveWorker 单卷模式 (OK + 信号触发 + undo 写入)
  - ArchiveWorker 按 category 模式
  - ArchiveWorker 取消
  - ArchiveWorker 失败处理
  - ArchiveWorker 没有 undo_stack 时不写
"""
from __future__ import annotations

from pathlib import Path

import pytest

from filemaster.core.archiver import ArchiveFormat, ArchiveResult
from filemaster.core.undo import OperationType, UndoEntry, UndoStack
from filemaster.workers.archiver import ArchiveWorker

# ---------------- Signal Recorder ----------------


class _SignalRecorder:
    """录 ArchiveWorker 5 个信号."""

    def __init__(self, worker: ArchiveWorker) -> None:
        self.progressed: list[tuple[int, str, int, int, str]] = []
        self.archive_done: list[ArchiveResult] = []
        self.cancelled: list[int] = []
        self.failed: list[tuple[str, str]] = []
        self.finished: list[list[ArchiveResult]] = []
        worker.progressed.connect(self._on_progressed)
        worker.archive_done.connect(self._on_archive_done)
        worker.cancelled.connect(self._on_cancelled)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)

    def _on_progressed(self, percent, name, i, t, msg) -> None:
        self.progressed.append((percent, name, i, t, msg))

    def _on_archive_done(self, result) -> None:
        self.archive_done.append(result)

    def _on_cancelled(self, count) -> None:
        self.cancelled.append(count)

    def _on_failed(self, name, err) -> None:
        self.failed.append((name, err))

    def _on_finished(self, results) -> None:
        self.finished.append(results)


# ---------------- Fixtures ----------------


@pytest.fixture
def sample_files(tmp_path: Path) -> list[Path]:
    d = tmp_path / "src"
    d.mkdir()
    files = []
    for i, ext in enumerate([".txt", ".txt", ".png", ".jpg"]):
        f = d / f"f{i}{ext}"
        f.write_text(f"file {i}")
        files.append(f)
    return files


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "out"


# ---------------- 单卷模式 ----------------


class TestSingleArchive:
    def test_ok_emits_signals(
        self, sample_files: list[Path], output_dir: Path
    ) -> None:
        undo = UndoStack()
        worker = ArchiveWorker(
            sample_files, output_dir, archive_name="test",
            fmt=ArchiveFormat.ZIP, undo_stack=undo,
        )
        rec = _SignalRecorder(worker)
        worker.run()

        # archive_done: 1 次
        assert len(rec.archive_done) == 1
        result = rec.archive_done[0]
        assert result.status == "OK"
        assert result.source_count == 4
        assert (output_dir / "test.zip").is_file()

        # progressed: 4 次 (每文件一次)
        assert len(rec.progressed) == 4
        # 第 1 个和最后 1 个
        assert rec.progressed[0][2] == 1
        assert rec.progressed[-1][2] == 4
        # 消息含 ETA
        assert "ETA" in rec.progressed[-1][4]

        # 没失败
        assert rec.failed == []
        # 没取消
        assert rec.cancelled == []

        # finished: 1 次, 含 1 个 result
        assert len(rec.finished) == 1
        assert len(rec.finished[0]) == 1

    def test_writes_undo_entry(
        self, sample_files: list[Path], output_dir: Path
    ) -> None:
        undo = UndoStack()
        worker = ArchiveWorker(
            sample_files, output_dir, archive_name="backup",
            undo_stack=undo,
        )
        _SignalRecorder(worker)
        worker.run()
        # UndoStack 应有 1 个 step
        assert len(undo) == 1
        step = undo.pop()
        assert len(step) == 1
        entry = step[0]
        assert entry.operation == "Archive"
        assert entry.target.name == "backup.zip"

    def test_no_undo_stack_works(
        self, sample_files: list[Path], output_dir: Path
    ) -> None:
        """不传 undo_stack 也能跑通."""
        worker = ArchiveWorker(
            sample_files, output_dir, archive_name="test",
        )
        rec = _SignalRecorder(worker)
        worker.run()
        assert rec.failed == []
        assert (output_dir / "test.zip").is_file()

    def test_tar_gz_format(
        self, sample_files: list[Path], output_dir: Path
    ) -> None:
        worker = ArchiveWorker(
            sample_files, output_dir, archive_name="test",
            fmt=ArchiveFormat.TAR_GZ,
        )
        rec = _SignalRecorder(worker)
        worker.run()
        assert rec.archive_done[0].status == "OK"
        assert (output_dir / "test.tar.gz").is_file()


# ---------------- 按 category 模式 ----------------


class TestByCategoryArchive:
    def test_emits_multiple_archive_done(
        self, sample_files: list[Path], output_dir: Path
    ) -> None:
        worker = ArchiveWorker(
            sample_files, output_dir,
            fmt=ArchiveFormat.ZIP, by_category=True,
        )
        rec = _SignalRecorder(worker)
        worker.run()
        # 至少 2 类 (IMAGE + TEXT/OTHER)
        assert len(rec.archive_done) >= 2
        # 全部 OK
        for r in rec.archive_done:
            assert r.status == "OK"
        # 对应文件应都创建
        for r in rec.archive_done:
            assert r.archive_path.is_file()

    def test_writes_undo_entries(
        self, sample_files: list[Path], output_dir: Path
    ) -> None:
        undo = UndoStack()
        worker = ArchiveWorker(
            sample_files, output_dir,
            fmt=ArchiveFormat.ZIP, by_category=True,
            undo_stack=undo,
        )
        _SignalRecorder(worker)
        worker.run()
        # 每个 category OK 写入 1 个 entry
        n_ok = sum(1 for r in worker._archiver.archive_by_category(
            sample_files, output_dir,
        ).values() if r.status == "OK")
        # 或者用更直接的: undo step 数量 == archive_done OK 数量
        assert len(undo) == 1
        step = undo.pop()
        assert len(step) == n_ok
        for entry in step:
            assert entry.operation == "Archive"


# ---------------- 取消 ----------------


class TestCancel:
    def test_cancel_before_run(
        self, sample_files: list[Path], output_dir: Path
    ) -> None:
        worker = ArchiveWorker(sample_files, output_dir)
        rec = _SignalRecorder(worker)
        worker.cancel()
        worker.run()
        # 1 个 CANCELLED 结果
        assert rec.archive_done[0].status == "CANCELLED"
        assert (output_dir / "archive.zip").exists() is False

    def test_cancellation_token_property(
        self, sample_files: list[Path], output_dir: Path
    ) -> None:
        worker = ArchiveWorker(sample_files, output_dir)
        token = worker.cancellation_token
        assert token.is_cancelled is False
        worker.cancel()
        assert token.is_cancelled is True


# ---------------- 失败处理 ----------------


class TestFailure:
    def test_archiver_exception(self, output_dir: Path) -> None:
        """没有文件也能跑 (会立刻 CANCELLED)."""
        worker = ArchiveWorker([], output_dir)
        rec = _SignalRecorder(worker)
        worker.run()
        # archive_done 会收到一个 ERROR 或 CANCELLED (取决于 archiver 内部)
        # 空 files 会让 archive_with_progress 返回 ERROR
        # worker.run() 仍会 finished.emit
        assert rec.failed == []  # 不应走 except 路径
        assert len(rec.finished) == 1
        assert rec.finished[0][0].status == "ERROR"


# ---------------- 基础属性 ----------------


class TestBasics:
    def test_worker_construction(
        self, sample_files: list[Path], output_dir: Path
    ) -> None:
        undo = UndoStack()
        worker = ArchiveWorker(
            sample_files, output_dir, archive_name="x",
            fmt=ArchiveFormat.ZIP, compression=9,
            by_category=False, base_dir=None, undo_stack=undo,
        )
        assert worker._archive_name == "x"
        assert worker._fmt is ArchiveFormat.ZIP
        assert worker._compression == 9
        assert worker._by_category is False
        assert worker._undo_stack is undo
        assert worker.cancellation_token is not None
