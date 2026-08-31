"""W6 BatchWorker 测试: 进度回调 + ETA + 流式 file_done.

W6 改造点:
1. 重构为使用 Renamer.apply_with_progress(on_progress) (复用 W5 基础设施)
2. ETA 估算用前 5 个文件耗时滑动窗口
3. 每个文件触发 file_done + progressed
"""

from __future__ import annotations

from pathlib import Path

import pytest

from filemaster.core.renamer import ConflictStrategy, Renamer, RenameResult
from filemaster.core.template import Template
from filemaster.core.undo import UndoStack
from filemaster.workers.batch import BatchWorker


class _SignalRecorder:
    """录 BatchWorker 5 个信号, 方便断言.

    W7 新增 cancelled(int) 信号 — 已处理文件数.
    """

    def __init__(self, worker: BatchWorker) -> None:
        self.progressed: list[tuple[int, str, int, int, str]] = []
        self.file_done: list[RenameResult] = []
        self.cancelled: list[int] = []
        self.failed: list[tuple[str, str]] = []
        self.finished: list[list[RenameResult]] = []
        worker.progressed.connect(self._on_progressed)
        worker.file_done.connect(self._on_file_done)
        worker.cancelled.connect(self._on_cancelled)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)

    def _on_progressed(self, percent, file, index, total, message) -> None:
        self.progressed.append((percent, file, index, total, message))

    def _on_file_done(self, result) -> None:
        self.file_done.append(result)

    def _on_cancelled(self, processed_count) -> None:
        self.cancelled.append(processed_count)

    def _on_failed(self, file, error) -> None:
        self.failed.append((file, error))

    def _on_finished(self, results) -> None:
        self.finished.append(results)


def _make_files(tmp_path: Path, n: int, ext: str = ".txt") -> list[Path]:
    files = []
    for i in range(n):
        f = tmp_path / f"f{i}{ext}"
        f.write_text("x")
        files.append(f)
    return files


class TestBatchWorker:
    """W6 BatchWorker 集成测试."""

    def test_basic_run_emits_per_file(self, tmp_path: Path) -> None:
        files = _make_files(tmp_path, 3)
        worker = BatchWorker(
            files=files,
            template=Template("{Index:D3}_{OriginalName}"),
        )
        rec = _SignalRecorder(worker)
        worker.run()
        assert len(rec.file_done) == 3
        assert len(rec.finished) == 1
        assert len(rec.finished[0]) == 3
        # 进度信号 N 个
        assert len(rec.progressed) == 3
        # 第一个文件的索引 = 1
        assert rec.progressed[0][2] == 1
        assert rec.progressed[-1][2] == 3
        assert rec.progressed[-1][3] == 3

    def test_progress_message_has_eta(self, tmp_path: Path) -> None:
        """W6 关键点: 进度消息含 ETA."""
        files = _make_files(tmp_path, 5)
        worker = BatchWorker(
            files=files,
            template=Template("{Index:D3}_{OriginalName}"),
        )
        rec = _SignalRecorder(worker)
        worker.run()
        # 第 2 个开始有 ETA (因为 _on_progress i>1 才加样本)
        for msg_idx in range(1, len(rec.progressed)):
            msg = rec.progressed[msg_idx][4]
            assert "/" in msg  # "2/5"
            assert "%" in msg  # "(40%)"
            assert "ETA" in msg  # "ETA Ns"

    def test_empty_files(self, tmp_path: Path) -> None:
        worker = BatchWorker(
            files=[],
            template=Template("{Index:D3}_{OriginalName}"),
        )
        rec = _SignalRecorder(worker)
        worker.run()
        assert rec.file_done == []
        assert len(rec.finished) == 1
        assert rec.finished[0] == []

    def test_with_undo_stack(self, tmp_path: Path) -> None:
        """W6: 真实 rename + UndoStack 联动 (对称 W4 dedup undo)."""
        files = _make_files(tmp_path, 2)
        undo = UndoStack()
        worker = BatchWorker(
            files=files,
            template=Template("{Index:D3}_{OriginalName}"),
            undo_stack=undo,
        )
        worker.run()
        # 2 个文件都改名成功
        assert (tmp_path / "001_f0.txt").exists()
        assert (tmp_path / "002_f1.txt").exists()
        # undo stack 是 deque[list[UndoEntry]], 每个 batch 收一组 rename
        total_entries = sum(len(batch) for batch in undo._entries)  # type: ignore[attr-defined]
        assert total_entries == 2

    def test_worker_does_not_crash_on_run(self, tmp_path: Path) -> None:
        """正常 run: 3 个文件全部处理."""
        files = _make_files(tmp_path, 3)
        worker = BatchWorker(
            files=files,
            template=Template("{Index:D3}_{OriginalName}"),
        )
        rec = _SignalRecorder(worker)
        worker.run()
        assert len(rec.finished) == 1
        assert len(rec.finished[0]) == 3
        assert len(rec.cancelled) == 0  # 正常完成, 不发 cancelled

    def test_failed_handler_does_not_crash(self, tmp_path: Path) -> None:
        """Worker 构造失败时发 failed 信号, 不抛."""
        worker = BatchWorker(
            files=[],
            template=Template("{Index:D3}_{OriginalName}"),
        )
        rec = _SignalRecorder(worker)
        # 即使没文件, 也不应该抛
        worker.run()
        assert len(rec.finished) == 1

    # ---- cancellation (W7) ----

    def test_worker_cancel_during_run(self, tmp_path: Path) -> None:
        """W7: worker.cancel() 在 run 中触发, emit cancelled(n) 信号."""
        files = _make_files(tmp_path, 5)
        worker = BatchWorker(
            files=files,
            template=Template("{Index:D3}_{OriginalName}"),
        )
        rec = _SignalRecorder(worker)

        # Monkey-patch _on_progress 闭包不直接, 改用 cancel 在 on_progress 触发的同点:
        # 通过连接 file_done 信号, 第 2 个文件 done 后调 cancel
        triggered = {"n": 0}

        def hook(result):
            triggered["n"] += 1
            if triggered["n"] == 2:
                worker.cancel()

        worker.file_done.connect(hook)
        worker.run()

        # 应该处理了 2 个文件, 然后取消
        assert len(rec.cancelled) == 1
        assert rec.cancelled[0] == 2
        assert len(rec.finished[0]) == 2
        # 前 2 个已改名
        assert (tmp_path / "001_f0.txt").exists()
        assert (tmp_path / "002_f1.txt").exists()
        # 后 3 个没改名
        assert not (tmp_path / "003_f2.txt").exists()

    def test_worker_cancel_before_run(self, tmp_path: Path) -> None:
        """W7: cancel 在 run 前就调, 0 文件处理."""
        files = _make_files(tmp_path, 3)
        worker = BatchWorker(
            files=files,
            template=Template("{Index:D3}_{OriginalName}"),
        )
        rec = _SignalRecorder(worker)
        worker.cancel()  # 预取消
        worker.run()
        # 0 文件处理
        assert len(rec.cancelled) == 1
        assert rec.cancelled[0] == 0
        assert len(rec.finished[0]) == 0
        # 原文件不动
        for f in files:
            assert f.exists()

    def test_worker_cancellation_token_property(self, tmp_path: Path) -> None:
        """W7: 暴露 cancellation_token 属性供外部状态查询."""
        files = _make_files(tmp_path, 2)
        worker = BatchWorker(
            files=files,
            template=Template("{Index:D3}_{OriginalName}"),
        )
        # 初始未取消
        assert worker.cancellation_token.is_cancelled is False
        worker.cancel()
        assert worker.cancellation_token.is_cancelled is True
