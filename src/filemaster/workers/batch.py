"""批处理后台 Worker.

W2 详细实现：
- QObject + QThread 模式
- CancellationToken 协作式取消（W7 接入 apply_with_progress）
- 进度信号实时回 UI
- 失败隔离（单文件失败不中断）
- 可选 UndoStack 联动
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from filemaster.core.cancellation import CancellationToken
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
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    Signals:
        progressed(percent, file, index, total, message)
        file_done(RenameResult)  # 每个文件结果（用于实时表格更新）
        cancelled(int)  # W7: 取消时发已处理的文件数
        finished(results)
        failed(file, error)
    """

    progressed = Signal(int, str, int, int, str)  # percent, file, index, total, message
    file_done = Signal(object)  # RenameResult
    cancelled = Signal(int)  # W7: 已处理的文件数
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
        # W7: 协作式取消令牌, 替代 W6 的 self._cancelled bool
        self._token = CancellationToken()

    def cancel(self) -> None:
        """请求取消（协作式, W7: 通过 CancellationToken 传给 apply_with_progress）."""
        self._token.cancel()

    @property
    def cancellation_token(self) -> CancellationToken:
        """暴露 token 供外部 (测试 / 状态查询) 使用."""
        return self._token

    def run(self) -> None:
        """执行批处理（在 QThread 内）.

        W6 重构: 改用 Renamer.apply_with_progress(on_progress) 复用 W5 的
        进度回调基础设施, 不再手动循环 apply([single_file]) (后者会破坏
        _index 连续性). ETA 估算用前 N 个文件的平均耗时.

        W7 扩展: 传 is_cancelled=lambda: self._token.is_cancelled 给引擎,
        让 apply_with_progress 在文件之间检查取消. 取消后发 cancelled(n) 信号.
        """
        try:
            renamer = Renamer(self._template, prefix=self._prefix)
            start_ts = time.monotonic()
            et_samples: list[float] = []  # 滑动窗口前 5 个文件耗时估算 ETA
            file_count = [0]  # list 以便 _on_progress 闭包修改

            def _on_progress(i: int, t: int, file: Path, result: RenameResult) -> None:
                file_count[0] = i
                # 1) 流式结果
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
            # W7: 传 is_cancelled, 引擎在文件之间检查
            results = renamer.apply_with_progress(
                self._files,
                conflict_strategy=self._conflict_strategy,
                undo_stack=self._undo_stack,
                on_progress=_on_progress,
                is_cancelled=lambda: self._token.is_cancelled,
            )

            # W7: 取消检测 — apply 返回的 results 数量 < 总文件数 说明被取消
            if self._token.is_cancelled and len(results) < len(self._files):
                self.cancelled.emit(len(results))

            self.finished.emit(results)
        except Exception as e:
            # Worker 级别崩溃（如 Renamer 构造失败）
            self.failed.emit("<worker>", str(e))
            self.finished.emit([])
