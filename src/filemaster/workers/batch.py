"""批处理后台 Worker.

W2 详细实现：
- QObject + QThread 模式
- CancellationPending 支持（协作式）
- 进度信号实时回 UI
- 失败隔离（单文件失败不中断）
- 可选 UndoStack 联动
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from filemaster.core.renamer import ConflictStrategy, Renamer, RenameResult
from filemaster.core.template import Template
from filemaster.core.undo import UndoStack


class BatchWorker(QObject):
    """批处理 Worker（QObject + QThread 模式）.

    用法（UI 侧）：
        self._thread = QThread()
        self._worker = BatchWorker(
            files, template,
            prefix="X_",
            conflict_strategy=ConflictStrategy.SKIP,
            undo_stack=self._undo_stack,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progressed.connect(self._on_progress)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    Signals:
        progressed(percent, file, index, total, message)
        file_done(RenameResult)  # 每个文件结果（用于实时表格更新）
        finished(results)
        failed(file, error)
    """

    progressed = Signal(int, str, int, int, str)  # percent, file, index, total, message
    file_done = Signal(object)  # RenameResult
    finished = Signal(list)  # List[RenameResult]
    failed = Signal(str, str)  # file, error

    def __init__(
        self,
        files: Iterable[Path],
        template: Template,
        prefix: str = "",
        conflict_strategy: ConflictStrategy = ConflictStrategy.SKIP,
        undo_stack: UndoStack | None = None,
    ) -> None:
        super().__init__()
        self._files = list(files)
        self._template = template
        self._prefix = prefix
        self._conflict_strategy = conflict_strategy
        self._undo_stack = undo_stack
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消（协作式）."""
        self._cancelled = True

    def run(self) -> None:
        """执行批处理（在 QThread 内）."""
        try:
            renamer = Renamer(self._template, prefix=self._prefix)
            results: list[RenameResult] = []
            total = len(self._files)

            # 真实执行（apply 会自动处理冲突 + 写 undo_stack）
            # 但我们想逐文件发信号，所以手动循环
            for i, file in enumerate(self._files, 1):
                if self._cancelled:
                    break
                try:
                    # 单文件 apply，复用同一个 renamer 保持 index 连续
                    single_results = renamer.apply(
                        [file],
                        conflict_strategy=self._conflict_strategy,
                        undo_stack=self._undo_stack,
                    )
                    result = single_results[0] if single_results else RenameResult(
                        file, None, "ERROR", "no result"
                    )
                    results.append(result)
                    self.file_done.emit(result)
                    self.progressed.emit(
                        int(i / total * 100) if total else 100,
                        file.name,
                        i,
                        total,
                        f"已处理 {i}/{total}",
                    )
                except Exception as e:
                    self.failed.emit(str(file), str(e))

            self.finished.emit(results)
        except Exception as e:
            # Worker 级别崩溃（如 Renamer 构造失败）
            self.failed.emit("<worker>", str(e))
            self.finished.emit([])
