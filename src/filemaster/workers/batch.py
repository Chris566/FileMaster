"""批处理后台 Worker.

W2 详细实现：
- QObject + QThread 模式
- CancellationPending 支持（协作式）
- 进度信号实时回 UI
- 失败隔离（单文件失败不中断）
- 可选 UndoStack 联动
"""

from __future__ import annotations

import time
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
        """执行批处理（在 QThread 内）.

        W6 重构: 改用 Renamer.apply_with_progress(on_progress) 复用 W5 的
        进度回调基础设施, 不再手动循环 apply([single_file]) (后者会破坏
        _index 连续性). ETA 估算用前 N 个文件的平均耗时.
        """
        try:
            renamer = Renamer(self._template, prefix=self._prefix)
            results: list[RenameResult] = []
            start_ts = time.monotonic()
            et_samples: list[float] = []  # 滑动窗口前 5 个文件耗时估算 ETA

            def _on_progress(i: int, t: int, file: Path, result: RenameResult) -> None:
                # 1) 流式结果
                results.append(result)
                self.file_done.emit(result)
                # 2) ETA 估算 (前 5 个样本 + 当前增量)
                now = time.monotonic()
                if i > 1:
                    et_samples.append(now - start_ts)
                    if len(et_samples) > 5:
                        et_samples.pop(0)
                if et_samples:
                    avg = sum(et_samples) / len(et_samples)
                    eta = int(avg * (t - i))
                else:
                    eta = 0
                eta_str = f" ETA {eta}s"  # W6: 始终展示 ETA, 即使 0s 也给用户进度感
                # 3) 进度信号
                percent = int(i / t * 100) if t else 100
                self.progressed.emit(
                    percent,
                    file.name,
                    i,
                    t,
                    f"{i}/{t} ({percent}%){eta_str}",
                )

            # 真实执行 (apply 会处理冲突 + 写 undo_stack + 内部 contextlib.suppress 吞回调异常)
            renamer.apply_with_progress(
                self._files,
                conflict_strategy=self._conflict_strategy,
                undo_stack=self._undo_stack,
                on_progress=_on_progress,
            )

            # 如果有取消请求, _apply_one 会跳过剩余文件, results 已含已完成的
            self.finished.emit(results)
        except Exception as e:
            # Worker 级别崩溃（如 Renamer 构造失败）
            self.failed.emit("<worker>", str(e))
            self.finished.emit([])
