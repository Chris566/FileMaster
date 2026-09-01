"""归档后台 Worker.

W10 详细实现:
- QObject + QThread 模式 (与 BatchWorker 一致)
- CancellationToken 协作式取消 (W7) + 硬中断 (W9 通过 safe_rename 落到 archiver)
- 进度信号实时回 UI
- 单次 archive / 按 category 分卷 两种模式
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from filemaster.core.archiver import (
    ArchiveEntry,
    ArchiveFormat,
    Archiver,
    ArchiveResult,
    cleanup_archive_tmps,
)
from filemaster.core.cancellation import CancellationToken
from filemaster.core.undo import UndoEntry, UndoStack


class ArchiveWorker(QObject):
    """归档 Worker (QObject + QThread 模式).

    用法 (UI 侧):
        self._thread = QThread()
        self._worker = ArchiveWorker(
            files, output_dir, fmt=ArchiveFormat.ZIP,
            by_category=False, undo_stack=self._undo_stack,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progressed.connect(self._on_progress)
        self._worker.archive_done.connect(self._on_archive_done)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    Signals:
        progressed(percent, archive_name, index, total, message)  # 进度
        archive_done(ArchiveResult)                                # 单卷完成
        cancelled(int)                                             # W7: 已处理文件数
        finished(list[ArchiveResult])                              # 全部完成
        failed(archive_name, error)                                # 失败
    """

    progressed = Signal(int, str, int, int, str)
    archive_done = Signal(object)  # ArchiveResult
    cancelled = Signal(int)
    finished = Signal(list)
    failed = Signal(str, str)

    def __init__(
        self,
        files: Iterable[Path],
        output_dir: Path,
        archive_name: str = "archive",
        fmt: ArchiveFormat = ArchiveFormat.ZIP,
        compression: int = 6,
        by_category: bool = False,
        base_dir: Path | None = None,
        undo_stack: UndoStack | None = None,
    ) -> None:
        super().__init__()
        self._files = list(files)
        self._output_dir = output_dir
        self._archive_name = archive_name
        self._fmt = fmt
        self._compression = compression
        self._by_category = by_category
        self._base_dir = base_dir
        self._undo_stack = undo_stack
        self._token = CancellationToken()
        self._archiver = Archiver()

    def cancel(self) -> None:
        """请求取消 (协作式, 通过 CancellationToken 传给 archiver)."""
        self._token.cancel()

    @property
    def cancellation_token(self) -> CancellationToken:
        return self._token

    def run(self) -> None:
        """执行归档 (在 QThread 内)."""
        try:
            # W9: 清理上次残留
            cleanup_archive_tmps(self._output_dir)

            results: list[ArchiveResult] = []
            entries: list[UndoEntry] = []

            if self._by_category:
                self._run_by_category(results, entries)
            else:
                self._run_single(results, entries)

            # 写 UndoStack (整批 1 个 step)
            if self._undo_stack is not None and entries:
                self._undo_stack.push(entries)

            if self._token.is_cancelled and any(
                r.status == "CANCELLED" for r in results
            ):
                cancelled_count = sum(r.source_count for r in results)
                self.cancelled.emit(cancelled_count)

            self.finished.emit(results)
        except Exception as e:
            self.failed.emit("<worker>", str(e))
            self.finished.emit([])

    def _run_single(
        self, results: list[ArchiveResult], entries: list[UndoEntry]
    ) -> None:
        """单次归档模式."""
        archive_path = self._output_dir / f"{self._archive_name}{self._fmt.extension}"
        start = time.monotonic()
        file_count = [0]

        def _on_progress(i: int, t: int, file: Path, written: int) -> None:
            file_count[0] = i
            now = time.monotonic()
            elapsed = now - start
            eta = int((elapsed / i) * (t - i)) if i > 0 else 0
            percent = int(i / t * 100) if t else 100
            msg = f"{i}/{t} ({percent}%) ETA {eta}s"
            self.progressed.emit(percent, file.name, i, t, msg)

        result = self._archiver.archive_with_progress(
            self._files, archive_path,
            fmt=self._fmt, compression=self._compression,
            base_dir=self._base_dir,
            on_progress=_on_progress,
            is_cancelled=lambda: self._token.is_cancelled,
        )
        results.append(result)
        self.archive_done.emit(result)

        if result.status == "OK" and self._undo_stack is not None:
            entries.append(UndoEntry(
                operation="Archive",
                target=result.archive_path,
            ))

    def _run_by_category(
        self, results: list[ArchiveResult], entries: list[UndoEntry]
    ) -> None:
        """按 category 分卷模式."""
        all_results = self._archiver.archive_by_category(
            self._files, self._output_dir,
            fmt=self._fmt, compression=self._compression,
            on_progress=self._on_category_progress,
            is_cancelled=lambda: self._token.is_cancelled,
        )
        # archive_by_category 返回 dict, 按 BUILTIN_CATEGORIES 顺序展平
        for _cat, result in all_results.items():
            results.append(result)
            self.archive_done.emit(result)
            if result.status == "OK" and self._undo_stack is not None:
                entries.append(UndoEntry(
                    operation="Archive",
                    target=result.archive_path,
                ))

    def _on_category_progress(
        self, category: str, i: int, t: int, file: Path, written: int
    ) -> None:
        """按 category 模式下的进度回调 (前缀 category 名)."""
        percent = int(i / t * 100) if t else 100
        msg = f"[{category}] {i}/{t} ({percent}%)"
        self.progressed.emit(percent, file.name, i, t, msg)
