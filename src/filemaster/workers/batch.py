"""批处理后台 Worker.

W7 详细实现：
- QThread 包装
- CancellationPending 支持
- 进度信号实时回 UI
- 失败隔离（单文件失败不中断）
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from filemaster.core.renamer import Renamer, RenameResult
from filemaster.core.template import Template


class BatchWorker(QObject):
    """批处理 Worker（QObject + QThread 模式）.

    用法（UI 侧）：
        self._thread = QThread()
        self._worker = BatchWorker(files, template, prefix)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()
    """

    progressed = Signal(int, str, int, str)  # percent, file, total, message
    finished = Signal(list)  # List[RenameResult]
    failed = Signal(str, str)  # file, error

    def __init__(
        self,
        files: list[Path],
        template: Template,
        prefix: str = "",
        dry_run: bool = True,
    ) -> None:
        super().__init__()
        self._files = files
        self._template = template
        self._prefix = prefix
        self._dry_run = dry_run
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消（协作式）."""
        self._cancelled = True

    def run(self) -> None:
        """执行批处理（在 QThread 内）."""
        renamer = Renamer(self._template, prefix=self._prefix)
        results: list[RenameResult] = []
        total = len(self._files)

        for i, file in enumerate(self._files, 1):
            if self._cancelled:
                break
            try:
                batch_result = renamer.plan([file])
                results.extend(batch_result)
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
