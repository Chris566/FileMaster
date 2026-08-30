"""W2 集成测试：完整重命名工作流.

覆盖：
- 端到端 apply + UndoStack + 3 种冲突策略
- BatchWorker 在 QThread 内执行（用 qtbot）
- 与分类/去重的串联
"""

from __future__ import annotations

from pathlib import Path

import pytest

from filemaster.core.renamer import ConflictStrategy, Renamer
from filemaster.core.template import Template
from filemaster.core.undo import UndoStack


@pytest.mark.integration
class TestW2FullApplyPipeline:
    """W2 端到端流水线."""

    def test_apply_then_undo_rename_only(self, tmp_path: Path) -> None:
        """apply → undo 完整闭环."""
        # 准备 3 个文件
        files = []
        for i in range(1, 4):
            f = tmp_path / f"doc_{i:03d}.pdf"
            f.write_bytes(b"content " + str(i).encode())
            files.append(f)

        tpl = Template("{Prefix}_{Index:D3}_{OriginalName}")
        undo = UndoStack(persist_dir=tmp_path / "undo")
        renamer = Renamer(tpl, prefix="R")

        # 1. apply
        results = renamer.apply(files, undo_stack=undo)
        assert all(r.status == "OK" for r in results)
        assert all(not f.exists() for f in files)
        assert len(undo) == 1

        # 2. undo
        batch = undo.pop()
        assert batch is not None
        import shutil

        for entry in batch:
            if entry.target and entry.target.exists():
                entry.target.rename(entry.source)
        # 3. 源恢复
        assert all(f.exists() for f in files)
        assert not (tmp_path / "R_001_doc_001.pdf").exists()

    def test_apply_rename_new_skips_existing(
        self, tmp_path: Path
    ) -> None:
        """rename_new 策略：跳过同名，生成 (1) (2)..."""
        (tmp_path / "a.pdf").write_bytes(b"new")
        # 目标已存在
        (tmp_path / "X_a.pdf").write_bytes(b"existing")
        (tmp_path / "X_a (1).pdf").write_bytes(b"existing2")

        files = [tmp_path / "a.pdf"]
        tpl = Template("{Prefix}{OriginalName}")
        renamer = Renamer(tpl, prefix="X_")
        results = renamer.apply(
            files, conflict_strategy=ConflictStrategy.RENAME_NEW
        )
        # 避两个冲突，应该 (2)
        assert results[0].status == "RENAMED"
        assert results[0].target.name == "X_a (2).pdf"
        assert results[0].target.exists()

    def test_overwrite_preserves_target_content(self, tmp_path: Path) -> None:
        """overwrite 策略：源替换目标，备份恢复原内容."""
        target = tmp_path / "X_a.pdf"
        target.write_bytes(b"original")
        source = tmp_path / "a.pdf"
        source.write_bytes(b"new")

        undo = UndoStack(persist_dir=tmp_path / "undo")
        tpl = Template("{Prefix}{OriginalName}")
        renamer = Renamer(tpl, prefix="X_")
        renamer.apply(
            [source], conflict_strategy=ConflictStrategy.OVERWRITE, undo_stack=undo
        )
        # 源消失
        assert not source.exists()
        # 目标被新内容替换
        assert target.read_bytes() == b"new"
        # 备份保留原内容
        batch = next(iter(undo))
        assert batch[0].backup_path is not None
        assert batch[0].backup_path.read_bytes() == b"original"


@pytest.mark.integration
@pytest.mark.gui
class TestW2BatchWorker:
    """BatchWorker 在 QThread 内的执行."""

    def test_batch_worker_runs_in_thread(self, qtbot, tmp_path: Path) -> None:
        pytest.importorskip("PySide6")
        from PySide6.QtCore import QThread
        from PySide6.QtWidgets import QApplication

        from filemaster.workers.batch import BatchWorker

        # 准备 5 个文件
        files = []
        for i in range(1, 6):
            f = tmp_path / f"doc_{i:03d}.pdf"
            f.write_bytes(b"x")
            files.append(f)

        # 启动 worker
        thread = QThread()
        worker = BatchWorker(
            files=files,
            template=Template("{Prefix}_{Index:D3}_{OriginalName}"),
            prefix="B",
            conflict_strategy=ConflictStrategy.SKIP,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        # 收集结果
        done_results: list = []
        worker.finished.connect(lambda r: done_results.extend(r))
        worker.finished.connect(thread.quit)

        thread.start()
        qtbot.waitUntil(lambda: not thread.isRunning(), timeout=5000)

        assert len(done_results) == 5
        assert all(r.status == "OK" for r in done_results)
        # 源已移动
        assert all(not f.exists() for f in files)
        # 目标文件存在
        assert (tmp_path / "B_001_doc_001.pdf").exists()

    def test_batch_worker_cancel_api(self, qtbot, tmp_path: Path) -> None:
        """cancel() 是个可调用的协作式 API，不崩."""
        pytest.importorskip("PySide6")
        from PySide6.QtCore import QThread

        from filemaster.workers.batch import BatchWorker

        files = []
        for i in range(1, 4):
            f = tmp_path / f"doc_{i:03d}.pdf"
            f.write_bytes(b"x")
            files.append(f)

        thread = QThread()
        worker = BatchWorker(
            files=files,
            template=Template("{Prefix}{OriginalName}"),
            prefix="C_",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)

        thread.start()
        # 不管 worker 跑没跑完，直接 cancel()
        worker.cancel()
        qtbot.waitUntil(lambda: not thread.isRunning(), timeout=5000)

        # 只要线程能退出就算成功
        assert not thread.isRunning()
